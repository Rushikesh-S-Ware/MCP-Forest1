import os
from pathlib import Path
from typing import Optional

def get_project_root() -> Path:
    """Find project root, with Docker-aware fallback."""
    # First check if we're in a Docker container with explicit root
    docker_root = os.environ.get("PROJECT_ROOT")
    if docker_root:
        return Path(docker_root)
    
    # Otherwise, find root by looking for marker files
    current = Path(__file__).resolve().parent
    while current != current.parent:  # Stop at filesystem root
        if (current / "pyproject.toml").exists() or (current / "README.md").exists():
            return current
        current = current.parent
    
    # Fallback for Docker: assume we're in a standard structure
    # Typically /app in Docker containers
    if Path("/app").exists():
        return Path("/app")
    
    raise RuntimeError("Project root not found")

class Settings:
    """Configuration settings with Docker support."""
    
    def __init__(self):
        # Base paths with environment variable override
        self.BASE_DIR = Path(os.environ.get("PROJECT_ROOT", get_project_root()))
        
        # Data directory configuration
        self.DATA_DIR = Path(os.environ.get("DATA_DIR", self.BASE_DIR / "data"))
        self.CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", self.BASE_DIR / "config"))
        
        # Ensure directories exist
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        
        # Data paths
        self.raw_data_path = self.DATA_DIR / "raw"
        self.processed_data_path = self.DATA_DIR / "processed"
        
        # Ensure data directories exist
        self.raw_data_path.mkdir(parents=True, exist_ok=True)
        self.processed_data_path.mkdir(parents=True, exist_ok=True)
        
        # File names with environment variable override
        self.excel_file = os.environ.get("EXCEL_FILE", "global_05212025.xlsx")
        self.database_name = os.environ.get("DATABASE_NAME", "forest.db")
        
        # Database path - can be overridden entirely via env var
        db_path_env = os.environ.get("DATABASE_PATH")
        if db_path_env:
            self.sqlite_db_path = Path(db_path_env)
        else:
            self.sqlite_db_path = self.processed_data_path / self.database_name
        
        # Ensure database directory exists
        self.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Metadata paths
        self.semantic_metadata_path = self.CONFIG_DIR / "semantic"
        self.runtime_metadata_path = self.CONFIG_DIR / "runtime"
        
        # Ensure metadata directories exist
        self.semantic_metadata_path.mkdir(parents=True, exist_ok=True)
        self.runtime_metadata_path.mkdir(parents=True, exist_ok=True)
        
        # Data validation thresholds
        self.min_completeness_score = float(os.environ.get("MIN_COMPLETENESS_SCORE", "0.70"))
        self.max_null_percentage = float(os.environ.get("MAX_NULL_PERCENTAGE", "0.40"))
        
        # Logging
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        
        # Server configuration
        self.mcp_host = os.environ.get("MCP_HOST", "0.0.0.0")
        self.mcp_port = int(os.environ.get("MCP_PORT", "8007"))
        
    def get_absolute_db_path(self) -> str:
        """Return absolute database path as string."""
        return str(self.sqlite_db_path.resolve())
    
    def validate_paths(self) -> bool:
        """Validate that critical paths exist."""
        issues = []
        
        if not self.DATA_DIR.exists():
            issues.append(f"Data directory not found: {self.DATA_DIR}")
        
        if not self.sqlite_db_path.exists():
            issues.append(f"Database not found: {self.sqlite_db_path}")
            
        if issues:
            print("Path validation issues:")
            for issue in issues:
                print(f"  - {issue}")
            return False
        return True
    
    def __str__(self) -> str:
        """String representation for debugging."""
        return f"""Settings Configuration:
  BASE_DIR: {self.BASE_DIR}
  DATA_DIR: {self.DATA_DIR}
  CONFIG_DIR: {self.CONFIG_DIR}
  Database: {self.sqlite_db_path}
  Excel File: {self.excel_file}
  Log Level: {self.log_level}
  MCP Server: {self.mcp_host}:{self.mcp_port}"""

# Initialize settings
settings = Settings()

# Optional: Print configuration in debug mode
if os.environ.get("DEBUG") == "true":
    print(settings)
    settings.validate_paths()