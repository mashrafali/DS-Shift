from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "DS Shift"
    app_version: str = "1.0 RC1"
    database_url: str = "postgresql+psycopg://dsshift:dsshift@database:5432/dsshift"
    cors_origins: str = "*"

    class Config:
        env_file = ".env"


settings = Settings()
