from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "DS Shift"
    app_version: str = "1.0 RC1"
    database_url: str = "postgresql+psycopg://dsshift:dsshift@database:5432/dsshift"
    cors_origins: str = "*"
    spark_engine_url: str = "http://spark-engine:8200"
    spark_preflight_timeout_seconds: int = 900
    max_active_migrations: int = 3
    max_active_migrations_per_connector: int = 3

    class Config:
        env_file = ".env"


settings = Settings()
