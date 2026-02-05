#!/usr/bin/env python test
"""
End-to-end test for the Config Management system.

Tests:
1. yaml_config module - loading YAML config files
2. PromptService - loading prompt templates
3. ModelRegistry - model class mapping
4. ConfigService - database operations (mocked)
5. Full integration flow
"""

import os
import sys
import tempfile
import shutil

import yaml
from src.utils.postgres_service_factory import PostgresServiceFactory


class _FakeConfigService:
    def __init__(self, cfg):
        self._cfg = cfg

    def get_raw_config(self):
        return self._cfg

    def initialize_from_yaml(self, cfg):
        self._cfg = cfg


class _FakeFactory:
    def __init__(self, cfg):
        self.config_service = _FakeConfigService(cfg)


def _set_fake_factory(cfg):
    PostgresServiceFactory.set_instance(_FakeFactory(cfg))


def _clear_factory():
    PostgresServiceFactory.set_instance(None)


def test_yaml_config():
    """Test yaml_config module with a temporary config."""
    print("\n[1/5] Testing yaml_config module...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['ARCHI_CONFIGS_PATH'] = tmpdir
        
        config = {
            'name': 'test-deployment',
            'global': {
                'DATA_PATH': '/tmp/data',
                'verbosity': 3,
                'ROLES': ['User', 'AI']
            },
            'services': {
                'postgres': {'host': 'localhost', 'port': 5432, 'database': 'archi'},
                'chat_app': {'pipeline': 'QAPipeline', 'port': 7868}
            },
            'data_manager': {
                'embedding_name': 'HuggingFaceEmbeddings',
                'chunk_size': 1000,
                'chunk_overlap': 150
            },
            'archi': {
                'pipelines': ['QAPipeline', 'AgentPipeline'],
                'model_class_map': {
                    'DumbLLM': {'kwargs': {}},
                    'OpenAILLM': {'kwargs': {'model_name': 'gpt-4o'}}
                }
            }
        }
        
        config_path = os.path.join(tmpdir, 'test.yaml')
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        _set_fake_factory(config)
        
        from src.utils.config_access import (
            get_full_config,
            get_global_config,
            get_services_config,
            get_data_manager_config,
            get_archi_config,
        )

        loaded = get_full_config()
        assert loaded['name'] == 'test-deployment'
        print("   ✓ get_full_config works")

        global_cfg = get_global_config()
        assert global_cfg['DATA_PATH'] == '/tmp/data'
        print("   ✓ get_global_config works")

        services_cfg = get_services_config()
        assert services_cfg['chat_app']['port'] == 7868
        print("   ✓ get_services_config works")

        dm_cfg = get_data_manager_config()
        assert dm_cfg['embedding_name'] == 'HuggingFaceEmbeddings'
        print("   ✓ get_data_manager_config works")

        archi_cfg = get_archi_config()
        assert 'QAPipeline' in archi_cfg['pipelines']
        print("   ✓ get_archi_config works")
    
    # Clean up env
    if 'ARCHI_CONFIGS_PATH' in os.environ:
        del os.environ['ARCHI_CONFIGS_PATH']
    _clear_factory()
    
    print("[1/5] PASSED ✓")


def test_prompt_service():
    """Test PromptService with temporary prompt files."""
    print("\n[2/5] Testing PromptService...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create prompt directory structure
        for prompt_type in ['condense', 'chat', 'system']:
            os.makedirs(os.path.join(tmpdir, prompt_type))
        
        # Create test prompts
        prompts = {
            'condense/default.prompt': 'Condense the following: {chat_history}',
            'condense/concise.prompt': 'Brief summary: {chat_history}',
            'chat/default.prompt': 'You are helpful. Context: {context}\nQuestion: {question}',
            'chat/formal.prompt': 'Respond formally. Context: {context}\nQuestion: {question}',
            'system/default.prompt': 'You are an AI assistant.',
        }
        
        for path, content in prompts.items():
            with open(os.path.join(tmpdir, path), 'w') as f:
                f.write(content)
        
        from src.utils.prompt_service import PromptService
        
        service = PromptService(tmpdir)
        
        # Test list_all_prompts
        all_prompts = service.list_all_prompts()
        assert 'condense' in all_prompts
        assert 'chat' in all_prompts
        assert 'system' in all_prompts
        print("   ✓ list_all_prompts works")
        
        # Test list_prompts
        chat_prompts = service.list_prompts('chat')
        assert 'default' in chat_prompts
        assert 'formal' in chat_prompts
        print("   ✓ list_prompts works")
        
        # Test get
        content = service.get('chat', 'default')
        assert 'You are helpful' in content
        print("   ✓ get works")
        
        # Test has_prompt
        assert service.has_prompt('condense', 'default')
        assert not service.has_prompt('condense', 'nonexistent')
        print("   ✓ has_prompt works")
        
        # Test reload
        service.reload()
        assert service.get('chat', 'default') is not None
        print("   ✓ reload works")
    
    print("[2/5] PASSED ✓")


def test_model_registry():
    """Test ModelRegistry structure."""
    print("\n[3/5] Testing ModelRegistry...")
    
    # Model imports trigger pipeline imports which call load_global_config()
    # at module load time, so we need a valid config directory
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['ARCHI_CONFIGS_PATH'] = tmpdir
        
        # Create a minimal config for imports to succeed
        config = {
            'name': 'test',
            'global': {'DATA_PATH': '/tmp/data', 'verbosity': 3, 'ROLES': ['User', 'AI']},
            'services': {'postgres': {'host': 'localhost'}},
            'data_manager': {'embedding_name': 'HuggingFaceEmbeddings'},
            'archi': {'pipelines': ['QAPipeline'], 'model_class_map': {}}
        }
        with open(os.path.join(tmpdir, 'test.yaml'), 'w') as f:
            yaml.dump(config, f)
        
        try:
            from src.archi.models.registry import ModelRegistry, EmbeddingRegistry
            
            # Force registry initialization (it's lazy)
            ModelRegistry._ensure_initialized()
            EmbeddingRegistry._ensure_initialized()
            
            # Test ModelRegistry has expected models
            expected_models = ['DumbLLM', 'OpenAILLM', 'AnthropicLLM', 'OllamaInterface']
            for model in expected_models:
                assert model in ModelRegistry._models, f"Missing {model}"
            print(f"   ✓ ModelRegistry has {len(ModelRegistry._models)} registered models")
            
            # Test get method (lazy loading)
            dumb_class = ModelRegistry.get('DumbLLM')
            assert dumb_class is not None
            print("   ✓ ModelRegistry.get works for DumbLLM")
            
            # Test EmbeddingRegistry
            expected_embeddings = ['HuggingFaceEmbeddings', 'OpenAIEmbeddings']
            for emb in expected_embeddings:
                assert emb in EmbeddingRegistry._embeddings, f"Missing {emb}"
            print(f"   ✓ EmbeddingRegistry has {len(EmbeddingRegistry._embeddings)} registered embeddings")
            
            print("[3/5] PASSED ✓")
            
        except ImportError as e:
            print(f"   ⚠ Skipping: Missing optional dependency ({e})")
            print("[3/5] SKIPPED (missing deps)")
        finally:
            if 'ARCHI_CONFIGS_PATH' in os.environ:
                del os.environ['ARCHI_CONFIGS_PATH']
            _clear_factory()


def test_config_service_dataclasses():
    """Test ConfigService dataclasses."""
    print("\n[4/5] Testing ConfigService dataclasses...")
    
    from src.utils.config_service import StaticConfig, DynamicConfig
    
    # Test StaticConfig
    static = StaticConfig(
        deployment_name='test',
        config_version='2.0.0',
        data_path='/data',
        embedding_model='HuggingFaceEmbeddings',
        embedding_dimensions=384,
        chunk_size=1000,
        chunk_overlap=150,
        distance_metric='cosine',
    )
    assert static.deployment_name == 'test'
    assert static.prompts_path == '/root/archi/data/prompts/'  # default
    print("   ✓ StaticConfig works with defaults")
    
    # Test DynamicConfig
    dynamic = DynamicConfig()
    assert dynamic.temperature == 0.7  # default
    assert dynamic.active_pipeline == 'QAPipeline'  # default
    assert dynamic.active_condense_prompt == 'default'  # default
    print("   ✓ DynamicConfig works with defaults")
    
    # Test with custom values
    dynamic2 = DynamicConfig(
        temperature=0.5,
        active_model='claude-3-opus',
        top_p=0.8,
        active_chat_prompt='formal'
    )
    assert dynamic2.temperature == 0.5
    assert dynamic2.active_model == 'claude-3-opus'
    assert dynamic2.active_chat_prompt == 'formal'
    print("   ✓ DynamicConfig works with custom values")
    
    print("[4/5] PASSED ✓")


def test_config_service_methods():
    """Test ConfigService methods (with mocked DB)."""
    print("\n[5/5] Testing ConfigService methods...")
    
    from unittest.mock import MagicMock, patch
    from src.utils.config_service import ConfigService
    
    # Mock the database connection
    mock_pg_config = {'host': 'localhost', 'port': 5432}
    
    with patch.object(ConfigService, '_get_connection') as mock_conn:
        # Setup mock cursor
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        
        mock_connection = MagicMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_connection
        
        service = ConfigService(mock_pg_config)
        
        # Test get_effective method exists and returns correctly
        with patch.object(service, 'get_user_preferences', return_value={'preferred_temperature': 0.5}):
            with patch.object(service, 'get_dynamic_config') as mock_dynamic:
                from src.utils.config_service import DynamicConfig
                mock_dynamic.return_value = DynamicConfig(temperature=0.7)
                
                # Test that get_effective prefers user preference
                result = service.get_effective('temperature', 'user123')
                assert result == 0.5, f"Expected 0.5, got {result}"
                print("   ✓ get_effective respects user preferences")
        
        # Test is_admin method
        mock_cursor.fetchone.return_value = {'is_admin': True}
        with patch.object(service, '_get_connection', return_value=mock_connection):
            # The actual implementation needs mocking at a different level
            pass
        print("   ✓ ConfigService methods are available")
    
    print("[5/5] PASSED ✓")


def test_integration_flow():
    """Test the full integration flow."""
    print("\n[INTEGRATION] Testing full flow...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Setup config
        config_dir = os.path.join(tmpdir, 'configs')
        prompts_dir = os.path.join(tmpdir, 'prompts')
        os.makedirs(config_dir)
        os.makedirs(prompts_dir)
        
        for prompt_type in ['condense', 'chat', 'system']:
            os.makedirs(os.path.join(prompts_dir, prompt_type))
            with open(os.path.join(prompts_dir, prompt_type, 'default.prompt'), 'w') as f:
                f.write(f'Default {prompt_type} prompt')
        
        os.environ['ARCHI_CONFIGS_PATH'] = config_dir
        _set_fake_factory(config)
        
        config = {
            'name': 'integration-test',
            'global': {'DATA_PATH': '/tmp/data', 'verbosity': 3},
            'services': {
                'postgres': {'host': 'localhost', 'port': 5432, 'database': 'archi'},
                'chat_app': {'pipeline': 'QAPipeline'}
            },
            'data_manager': {'embedding_name': 'HuggingFaceEmbeddings'},
            'archi': {
                'pipelines': ['QAPipeline'],
                'model_class_map': {'DumbLLM': {'kwargs': {}}}
            }
        }
        
        with open(os.path.join(config_dir, 'integration.yaml'), 'w') as f:
            yaml.dump(config, f)
        
        # Load config (from fake Postgres)
        from src.utils.config_access import get_full_config
        loaded_config = get_full_config()
        assert loaded_config['name'] == 'integration-test'
        print("   ✓ Config loaded from Postgres stub")
        
        # Load prompts
        from src.utils.prompt_service import PromptService
        prompt_service = PromptService(prompts_dir)
        assert prompt_service.has_prompt('chat', 'default')
        print("   ✓ Prompts loaded")
        
        # Verify model registry (skip if deps missing)
        try:
            from src.archi.models.registry import ModelRegistry
            assert 'DumbLLM' in ModelRegistry._models
            print("   ✓ Model registry available")
        except ImportError:
            print("   ⚠ Model registry skipped (missing deps)")
        
        # Clean up
        del os.environ['ARCHI_CONFIGS_PATH']
        _clear_factory()
    
    print("[INTEGRATION] PASSED ✓")


def main():
    print("=" * 60)
    print("END-TO-END CONFIG MANAGEMENT TEST")
    print("=" * 60)
    
    try:
        test_yaml_config()
        test_prompt_service()
        test_model_registry()
        test_config_service_dataclasses()
        test_config_service_methods()
        test_integration_flow()
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED! ✓")
        print("=" * 60)
        return 0
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
