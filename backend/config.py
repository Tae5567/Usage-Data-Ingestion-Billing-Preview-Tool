from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://billing:billing_secret@localhost:5432/billing_preview"
    OPENAI_API_KEY: str = ""
    CORS_ORIGINS: str = "http://localhost:3000"
    DEBUG: bool = True

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
print(f"DB URL loaded: {settings.DATABASE_URL[:30]}...")