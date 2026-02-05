"""
PromptService - Manages prompt files and caching.

Prompts are stored as files for version control, but can be
selected dynamically at the deployment or user level.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Prompt:
    """A loaded prompt."""
    name: str
    prompt_type: str  # 'condense', 'chat', 'system'
    content: str
    file_path: str


class PromptNotFoundError(Exception):
    """Raised when a prompt file is not found."""
    pass


class PromptService:
    """
    Service for managing prompt files.
    
    Directory structure:
        /root/archi/data/prompts/
        ├── condense/
        │   ├── default.prompt
        │   └── concise.prompt
        ├── chat/
        │   ├── default.prompt
        │   └── formal.prompt
        └── system/
            ├── default.prompt
            └── custom.prompt
    
    Example:
        >>> service = PromptService("/root/archi/data/prompts")
        >>> service.get("chat", "default")
        "You are a helpful assistant..."
        
        >>> service.list_prompts("chat")
        ["default", "formal", "technical"]
        
        >>> service.reload()  # Reload all prompts from disk
    """
    
    VALID_TYPES = ["condense", "chat", "system"]
    EXTENSION = ".prompt"
    
    def __init__(self, prompts_path: str):
        """
        Initialize PromptService.
        
        Args:
            prompts_path: Path to the prompts directory
        """
        self._prompts_path = Path(prompts_path)
        self._cache: Dict[str, Dict[str, Prompt]] = {}
        self._loaded = False
    
    def _ensure_loaded(self) -> None:
        """Ensure prompts are loaded."""
        if not self._loaded:
            self.reload()
    
    def reload(self) -> int:
        """
        Reload all prompts from disk.
        
        Returns:
            Number of prompts loaded
        """
        self._cache = {prompt_type: {} for prompt_type in self.VALID_TYPES}
        count = 0
        
        if not self._prompts_path.exists():
            logger.warning(f"Prompts directory not found: {self._prompts_path}")
            self._loaded = True
            return 0
        
        for prompt_type in self.VALID_TYPES:
            type_dir = self._prompts_path / prompt_type
            if not type_dir.exists():
                continue
            
            for file_path in type_dir.glob(f"*{self.EXTENSION}"):
                name = file_path.stem
                try:
                    content = file_path.read_text(encoding="utf-8").strip()
                    self._cache[prompt_type][name] = Prompt(
                        name=name,
                        prompt_type=prompt_type,
                        content=content,
                        file_path=str(file_path),
                    )
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to load prompt {file_path}: {e}")
        
        self._loaded = True
        logger.info(f"Loaded {count} prompts from {self._prompts_path}")
        return count
    
    def get(self, prompt_type: str, name: str) -> str:
        """
        Get a prompt by type and name.
        
        Args:
            prompt_type: Type of prompt ('condense', 'chat', 'system')
            name: Name of the prompt (without extension)
            
        Returns:
            The prompt content
            
        Raises:
            PromptNotFoundError: If prompt not found
            ValueError: If invalid prompt type
        """
        if prompt_type not in self.VALID_TYPES:
            raise ValueError(f"Invalid prompt type: {prompt_type}. Must be one of {self.VALID_TYPES}")
        
        self._ensure_loaded()
        
        prompts = self._cache.get(prompt_type, {})
        if name not in prompts:
            available = list(prompts.keys()) if prompts else []
            raise PromptNotFoundError(
                f"Prompt not found: {prompt_type}/{name}. Available: {available}"
            )
        
        return prompts[name].content
    
    def get_prompt(self, prompt_type: str, name: str) -> Optional[Prompt]:
        """
        Get a Prompt object by type and name.
        
        Args:
            prompt_type: Type of prompt
            name: Name of the prompt
            
        Returns:
            The Prompt object, or None if not found
        """
        if prompt_type not in self.VALID_TYPES:
            return None
        
        self._ensure_loaded()
        return self._cache.get(prompt_type, {}).get(name)
    
    def list_prompts(self, prompt_type: str) -> List[str]:
        """
        List available prompts of a given type.
        
        Args:
            prompt_type: Type of prompt
            
        Returns:
            List of prompt names
        """
        if prompt_type not in self.VALID_TYPES:
            return []
        
        self._ensure_loaded()
        return sorted(self._cache.get(prompt_type, {}).keys())
    
    def list_all_prompts(self) -> Dict[str, List[str]]:
        """
        List all available prompts by type.
        
        Returns:
            Dict mapping prompt type to list of names
        """
        self._ensure_loaded()
        return {
            prompt_type: sorted(prompts.keys())
            for prompt_type, prompts in self._cache.items()
        }
    
    def has_prompt(self, prompt_type: str, name: str) -> bool:
        """Check if a prompt exists."""
        if prompt_type not in self.VALID_TYPES:
            return False
        self._ensure_loaded()
        return name in self._cache.get(prompt_type, {})
    
    @property
    def prompts_path(self) -> str:
        """Get the prompts directory path."""
        return str(self._prompts_path)
