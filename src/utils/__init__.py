"""
archi utilities module.

Exports core utility services for PostgreSQL-consolidated architecture.
"""

# PostgreSQL connection pooling
from src.utils.connection_pool import (
    ConnectionPool,
    ConnectionPoolError,
    ConnectionTimeoutError,
)

# Configuration services
from src.utils.config_service import (
    ConfigService,
    StaticConfig,
    DynamicConfig,
    ConfigValidationError,
)

# User management
from src.utils.user_service import (
    UserService,
    User,
)

# Document selection (3-tier system)
from src.utils.document_selection_service import (
    DocumentSelectionService,
    DocumentSelection,
)

# Conversation tracking
from src.utils.conversation_service import (
    ConversationService,
    Message,
    ABComparison,
)

# Service factory
from src.utils.postgres_service_factory import (
    PostgresServiceFactory,
    create_services,
)

__all__ = [
    # Connection pool
    'ConnectionPool',
    'ConnectionPoolError', 
    'ConnectionTimeoutError',
    # Config
    'ConfigService',
    'StaticConfig',
    'DynamicConfig',
    'ConfigValidationError',
    # Users
    'UserService',
    'User',
    # Document selection
    'DocumentSelectionService',
    'DocumentSelection',
    # Conversations
    'ConversationService',
    'Message',
    'ABComparison',
    # Factory
    'PostgresServiceFactory',
    'create_services',
]

