import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Densito-AI"
    secret_key: str = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 8

    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./data/densito.db")
    storage_dir: str = os.environ.get("STORAGE_DIR", "./storage")

    # Учётка администратора, создаваемая при первом старте, если БД пустая
    bootstrap_admin_username: str = os.environ.get("BOOTSTRAP_ADMIN_USER", "admin")
    bootstrap_admin_password: str = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "admin12345")
    bootstrap_expert_username: str = os.environ.get("BOOTSTRAP_EXPERT_USER", "expert")
    bootstrap_expert_password: str = os.environ.get("BOOTSTRAP_EXPERT_PASSWORD", "expert12345")

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    class Config:
        env_file = ".env"


settings = Settings()

os.makedirs(os.path.dirname(settings.database_url.replace("sqlite:///", "")) or ".", exist_ok=True)
os.makedirs(settings.storage_dir, exist_ok=True)
