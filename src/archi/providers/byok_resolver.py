"""
BYOK (Bring Your Own Key) Provider Resolver.

Resolves API keys for LLM providers by checking:
1. User's BYOK API key (if user_id provided)
2. Environment variable (fallback)

This enables per-user API keys stored encrypted in PostgreSQL.
"""

from typing import Any, Dict, Optional

from src.archi.providers import get_provider
from src.archi.providers.base import BaseProvider, ProviderConfig, ProviderType
from src.utils.logging import get_logger

logger = get_logger(__name__)


class BYOKResolver:
    """
    Resolves providers with user-specific BYOK API keys.
    
    Example:
        >>> from src.utils.user_service import UserService
        >>> resolver = BYOKResolver(user_service=user_service)
        >>> provider = resolver.get_provider_for_user("openai", user_id="user_123")
        >>> model = provider.get_chat_model("gpt-4o")
    """
    
    # Map provider types to their BYOK column names in users table
    PROVIDER_TO_BYOK = {
        ProviderType.OPENAI: "openai",
        ProviderType.ANTHROPIC: "anthropic",
        ProviderType.OPENROUTER: "openrouter",
    }
    
    def __init__(self, user_service=None, pg_config: Optional[Dict[str, Any]] = None):
        """
        Initialize BYOK resolver.
        
        Args:
            user_service: UserService instance (preferred)
            pg_config: PostgreSQL config (creates UserService if user_service not provided)
        """
        self._user_service = user_service
        self._pg_config = pg_config
        
        # Lazy initialization
        self._user_service_initialized = user_service is not None
    
    def _get_user_service(self):
        """Lazily initialize UserService if needed."""
        if self._user_service is not None:
            return self._user_service
        
        if not self._user_service_initialized and self._pg_config:
            from src.utils.user_service import UserService
            self._user_service = UserService(self._pg_config)
            self._user_service_initialized = True
        
        return self._user_service
    
    def get_byok_key(
        self,
        provider_type: str | ProviderType,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Get BYOK API key for a user and provider.
        
        Args:
            provider_type: Provider type (string or ProviderType)
            user_id: User ID to lookup BYOK key for
            
        Returns:
            Decrypted API key if user has one set, None otherwise
        """
        if user_id is None:
            return None
        
        user_service = self._get_user_service()
        if user_service is None:
            logger.debug("UserService not available, BYOK lookup skipped")
            return None
        
        # Convert to ProviderType
        if isinstance(provider_type, str):
            try:
                provider_type = ProviderType(provider_type.lower())
            except ValueError:
                return None
        
        # Get BYOK provider name
        byok_provider = self.PROVIDER_TO_BYOK.get(provider_type)
        if byok_provider is None:
            logger.debug(f"Provider {provider_type} does not support BYOK")
            return None
        
        try:
            key = user_service.get_api_key(user_id, byok_provider)
            if key:
                logger.info(f"Using BYOK API key for user {user_id}, provider {byok_provider}")
            return key
        except Exception as e:
            logger.warning(f"Failed to retrieve BYOK key: {e}")
            return None
    
    def get_provider_for_user(
        self,
        provider_type: str | ProviderType,
        user_id: Optional[str] = None,
        use_cache: bool = False,  # Don't cache user-specific providers
        **kwargs,
    ) -> BaseProvider:
        """
        Get a provider instance, using user's BYOK key if available.
        
        The resolution order is:
        1. User's BYOK API key (if user_id provided and key exists)
        2. Environment variable / default provider configuration
        
        Args:
            provider_type: Provider type
            user_id: User ID for BYOK lookup (optional)
            use_cache: Whether to cache provider (default False for BYOK)
            **kwargs: Additional arguments for provider
            
        Returns:
            Provider instance with appropriate API key configured
        """
        # Try to get BYOK key
        byok_key = self.get_byok_key(provider_type, user_id)
        
        if byok_key:
            # Create provider with BYOK key
            # Don't use cache for user-specific keys
            config = ProviderConfig(
                provider_type=ProviderType(provider_type) if isinstance(provider_type, str) else provider_type,
                api_key=byok_key,
                **kwargs.get("config_kwargs", {}),
            )
            return get_provider(provider_type, config=config, use_cache=False)
        else:
            # Fall back to default provider (cached)
            return get_provider(provider_type, use_cache=use_cache)
    
    def get_chat_model_for_user(
        self,
        provider_type: str | ProviderType,
        model_name: str,
        user_id: Optional[str] = None,
        **model_kwargs,
    ):
        """
        Convenience method to get a chat model with BYOK resolution.
        
        Args:
            provider_type: Provider type
            model_name: Model name/ID
            user_id: User ID for BYOK lookup (optional)
            **model_kwargs: Additional model configuration
            
        Returns:
            Chat model instance (BaseChatModel)
        """
        provider = self.get_provider_for_user(provider_type, user_id=user_id)
        return provider.get_chat_model(model_name, **model_kwargs)


# Singleton instance for convenience
_default_resolver: Optional[BYOKResolver] = None


def get_byok_resolver(
    user_service=None,
    pg_config: Optional[Dict[str, Any]] = None,
) -> BYOKResolver:
    """
    Get or create the default BYOK resolver.
    
    Args:
        user_service: UserService instance (optional)
        pg_config: PostgreSQL config (optional, for creating UserService)
        
    Returns:
        BYOKResolver instance
    """
    global _default_resolver
    
    if _default_resolver is None or user_service is not None or pg_config is not None:
        _default_resolver = BYOKResolver(
            user_service=user_service,
            pg_config=pg_config,
        )
    
    return _default_resolver


def resolve_provider_for_user(
    provider_type: str | ProviderType,
    user_id: Optional[str] = None,
) -> BaseProvider:
    """
    Convenience function to resolve a provider for a user.
    
    Uses the default resolver. For more control, use BYOKResolver directly.
    
    Args:
        provider_type: Provider type
        user_id: User ID for BYOK lookup
        
    Returns:
        Provider instance
    """
    return get_byok_resolver().get_provider_for_user(provider_type, user_id=user_id)
