from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://radlab:radlab@localhost:5432/radlab"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:4566"
    s3_bucket: str = "radlab-artifacts"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    aws_default_region: str = "us-east-1"
    grpc_port: int = 50051
    health_port: int = 8081
    sim_mode: str = "auto"
    solver_root: str = ""
    openradioss_path: str = ""
    worker_concurrency: int = 2
    work_root: str = "/tmp/radlab"
    keep_workdir: bool = False
    mock_pace_s: float = 0.0
    queue_key: str = "radlab:jobs"

    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        return url


def get_settings() -> Settings:
    return Settings()
