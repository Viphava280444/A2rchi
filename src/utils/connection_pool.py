"""
ConnectionPool - PostgreSQL connection pool manager using psycopg2.pool.

Implements Connection Pool Management requirements:
- Connection reuse from pool
- Configurable pool size (default: 5 min, 20 max)
- Connection timeout handling with graceful 503 responses
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional

import psycopg2
import psycopg2.pool

from src.utils.logging import get_logger

logger = get_logger(__name__)


class ConnectionPoolError(Exception):
    """Raised when connection pool operations fail."""
    pass


class ConnectionTimeoutError(ConnectionPoolError):
    """Raised when a connection cannot be acquired within timeout."""
    pass


class ConnectionPool:
    """
    Thread-safe PostgreSQL connection pool.
    
    Provides efficient connection reuse across multiple requests.
    Uses psycopg2's ThreadedConnectionPool for thread safety.
    
    Example:
        >>> pool = ConnectionPool(pg_config, min_conn=5, max_conn=20)
        >>> with pool.get_connection() as conn:
        ...     with conn.cursor() as cur:
        ...         cur.execute("SELECT 1")
        >>> pool.close()
    
    Or use the singleton pattern:
        >>> pool = ConnectionPool.get_instance(pg_config)
        >>> with pool.get_connection() as conn:
        ...     # use connection
    """
    
    _instance: Optional["ConnectionPool"] = None
    _lock = threading.Lock()
    
    def __init__(
        self,
        pg_config: Optional[Dict[str, Any]] = None,
        *,
        connection_params: Optional[Dict[str, Any]] = None,
        min_conn: int = 5,
        max_conn: int = 20,
        timeout: float = 30.0,
    ):
        """
        Initialize connection pool.
        
        Args:
            pg_config: PostgreSQL connection parameters (host, port, user, password, dbname)
            connection_params: Alias for pg_config (for backward compatibility)
            min_conn: Minimum connections to keep open (default: 5)
            max_conn: Maximum connections allowed (default: 20)
            timeout: Timeout in seconds when acquiring connection (default: 30)
        
        Note:
            Either pg_config or connection_params must be provided.
        """
        effective_config = pg_config or connection_params
        if not effective_config:
            raise ValueError("Either pg_config or connection_params must be provided")
        self._pg_config = effective_config
        self._min_conn = min_conn
        self._max_conn = max_conn
        self._timeout = timeout
        self._pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
        self._closed = False
        
        self._initialize_pool()
    
    def _initialize_pool(self) -> None:
        """Initialize the connection pool."""
        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=self._min_conn,
                maxconn=self._max_conn,
                **self._pg_config,
            )
            logger.info(
                f"Connection pool initialized: min={self._min_conn}, max={self._max_conn}"
            )
        except psycopg2.Error as e:
            logger.error(f"Failed to initialize connection pool: {e}")
            raise ConnectionPoolError(f"Failed to initialize pool: {e}") from e
    
    @classmethod
    def get_instance(
        cls,
        pg_config: Optional[Dict[str, Any]] = None,
        *,
        connection_params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "ConnectionPool":
        """
        Get or create singleton pool instance.
        
        Thread-safe singleton pattern for application-wide connection pool.
        
        Args:
            pg_config: PostgreSQL config (required on first call)
            connection_params: Alias for pg_config (for backward compatibility)
            **kwargs: Additional args for pool initialization
            
        Returns:
            Singleton ConnectionPool instance
        """
        effective_config = pg_config or connection_params
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    if effective_config is None:
                        raise ValueError("pg_config or connection_params required for first initialization")
                    cls._instance = cls(pg_config=effective_config, **kwargs)
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.close()
                cls._instance = None
    
    @contextmanager
    def get_connection(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """
        Get a connection from the pool.
        
        Implements:
        - Connection reuse from pool
        - Connection timeout handling
        
        Usage:
            with pool.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
        
        Yields:
            Database connection
            
        Raises:
            ConnectionTimeoutError: If timeout waiting for connection
            ConnectionPoolError: If pool is closed or other error
        """
        if self._closed or self._pool is None:
            raise ConnectionPoolError("Connection pool is closed")
        
        conn = None
        try:
            conn = self._pool.getconn()
            
            if conn is None:
                raise ConnectionTimeoutError(
                    f"Could not acquire connection within {self._timeout}s timeout"
                )
            
            if conn.closed:
                conn = self._reconnect(conn)
            
            yield conn
            
        except psycopg2.pool.PoolError as e:
            logger.error(f"Pool error: {e}")
            raise ConnectionTimeoutError(f"Connection pool exhausted: {e}") from e
        except psycopg2.Error as e:
            logger.error(f"Database error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn is not None and self._pool is not None:
                try:
                    self._pool.putconn(conn)
                except Exception as e:
                    logger.warning(f"Error returning connection to pool: {e}")
    
    def _reconnect(self, conn: psycopg2.extensions.connection) -> psycopg2.extensions.connection:
        """Reconnect a closed connection."""
        logger.warning("Reconnecting closed connection")
        try:
            conn = psycopg2.connect(**self._pg_config)
            return conn
        except psycopg2.Error as e:
            raise ConnectionPoolError(f"Failed to reconnect: {e}") from e
    
    def execute(
        self,
        query: str,
        params: Optional[tuple] = None,
        *,
        fetch: bool = True,
    ) -> Any:
        """
        Execute a query using a pooled connection.
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                if fetch:
                    return cur.fetchall()
                conn.commit()
                return None
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        if self._pool is None:
            return {"status": "closed"}
        
        return {
            "status": "open",
            "min_connections": self._min_conn,
            "max_connections": self._max_conn,
            "timeout": self._timeout,
        }
    
    def close(self) -> None:
        """Close all connections in the pool."""
        if self._pool is not None and not self._closed:
            self._pool.closeall()
            self._closed = True
            logger.info("Connection pool closed")

    # Compatibility helpers for legacy callers
    def release_connection(self, conn) -> None:
        if self._pool is not None and conn is not None:
            try:
                self._pool.putconn(conn)
            except Exception:
                pass

    def get_connection_direct(self):
        if self._pool is None or self._closed:
            raise ConnectionPoolError("Connection pool is closed")
        return self._pool.getconn()
    
    def __enter__(self) -> "ConnectionPool":
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# Convenience function for Flask/service initialization
def init_pool(
    pg_config: Dict[str, Any],
    *,
    min_conn: int = 5,
    max_conn: int = 20,
) -> ConnectionPool:
    return ConnectionPool.get_instance(
        pg_config,
        min_conn=min_conn,
        max_conn=max_conn,
    )


def get_pool() -> ConnectionPool:
    pool = ConnectionPool.get_instance()
    if pool is None:
        raise ConnectionPoolError("Connection pool not initialized")
    return pool
