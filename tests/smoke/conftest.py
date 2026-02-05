"""
Pytest fixtures for smoke tests.
"""

import os
import uuid
import pytest

# PostgreSQL connection config for test container
PG_CONFIG = {
    "host": "localhost",
    "port": 5439,
    "database": "archi",
    "user": "archi",
    "password": "testpassword123",
}


@pytest.fixture(scope="module")
def test_user_id():
    """Create a test user and return the ID."""
    os.environ["BYOK_ENCRYPTION_KEY"] = "test-encryption-key-32chars-ok"
    
    from src.utils.user_service import UserService
    
    service = UserService(PG_CONFIG)
    user_id = f"test_user_{uuid.uuid4().hex[:8]}"
    service.get_or_create_user(user_id, auth_provider="anonymous")
    
    return user_id


@pytest.fixture(scope="module")
def test_conv_id(test_user_id):
    """Create a test conversation and return the ID."""
    from src.utils.conversation_service import ConversationService, Message
    
    service = ConversationService(connection_params=PG_CONFIG)
    conv_id = str(uuid.uuid4().int % 1000000)
    
    # Add a message to create the conversation
    msg = Message(
        conversation_id=conv_id,
        sender="user",
        content="Test message",
        archi_service="test",
    )
    service.insert_message(msg)
    
    return conv_id
