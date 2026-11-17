#!/usr/bin/env python3
"""
Run all AdCP services in a single process for container deployment.
This is the main entrypoint for Docker containers.

Handles:
- Environment validation
- Database connectivity checks
- Database migrations and initialization
- Starting MCP server, Admin UI, A2A server, nginx, and cron
"""

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from src.core.config import get_settings, load_settings

# The services this launcher started, by name, for supervision and cleanup
services: dict[str, subprocess.Popen] = {}

# Why a service this launcher was asked to run never started. A service thread appends
# here; the supervisor reads it.
start_failures: list[str] = []

# How long a stop waits for the services before it kills them. Below Docker's default
# 10-second stop grace, so the launcher still reports its own exit code.
STOP_GRACE_SECONDS = 8.0

# The launcher's exit code once it starts shutting down. A service failure sets it to 1
# first, so a stop signal that arrives during that shutdown cannot report a clean stop.
_exit_code = 0


def register_service(name: str, proc: subprocess.Popen) -> None:
    """Record a started service so the launcher watches it and stops it on exit."""
    services[name] = proc


def start_service_thread(name: str, run: Callable[[], None]) -> threading.Thread:
    """Run a service's start-and-monitor function in a thread, recording a failure to start.

    A thread that raises ends on its own and takes nothing down with it, so without this a
    missing binary or a refused configuration leaves the launcher running without the
    service. ``SystemExit`` is caught too: a ``sys.exit`` in a thread ends only the thread.
    """

    def guarded() -> None:
        try:
            run()
        except (Exception, SystemExit) as error:
            start_failures.append(f"{name} did not start: {error!r}")

    thread = threading.Thread(target=guarded, daemon=True)
    thread.start()
    return thread


def validate_required_env():
    """Read the environment once for this entrypoint and check what a deployment must set."""
    print("🔍 Validating required environment variables...")

    # The composition root of the container: a malformed value fails here, before any
    # service starts. Every later read in this script is off the same object.
    settings = load_settings()
    missing = []

    # Note: SUPER_ADMIN_EMAILS is optional - per-tenant OIDC with Setup Mode is the default auth flow.
    # New tenants start with auth_setup_mode=true, allowing test credentials to configure SSO.

    # Database URL is required
    if not settings.database.database_url:
        missing.append("DATABASE_URL")

    # Encryption key is required for storing OIDC client secrets
    if not settings.auth.encryption_key:
        missing.append(
            "ENCRYPTION_KEY (generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
        )

    # Multi-tenant mode requires SALES_AGENT_DOMAIN
    if settings.runtime.adcp_multi_tenant and not settings.runtime.sales_agent_domain:
        missing.append("SALES_AGENT_DOMAIN (required for multi-tenant mode)")

    if missing:
        print("❌ Missing required environment variables:")
        for var in missing:
            print(f"   - {var}")
        print("")
        print("📖 See docs/deployment.md for configuration details.")
        sys.exit(1)

    print("✅ Required environment variables are set")


def check_database_health():
    """Check if database is accessible."""
    print("🔍 Checking database connectivity...")

    # Import here to avoid issues if database modules aren't available
    try:
        from src.core.database.db_config import DatabaseConfig, get_db_connection
    except ImportError as e:
        print(f"❌ Failed to import database modules: {e}")
        sys.exit(1)

    # Show parsed connection info (without password)
    db_url = get_settings().database.database_url
    print(f"DATABASE_URL set: {bool(db_url)}")

    if db_url:
        try:
            config = DatabaseConfig.get_db_config()
            print(f"Parsed host: {config.get('host', 'NOT SET')}")
            print(f"Parsed port: {config.get('port', 'NOT SET')}")
            print(f"Parsed database: {config.get('database', 'NOT SET')}")
        except Exception as e:
            print(f"⚠️ Could not parse database config: {e}")

    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT 1")
        cursor.fetchone()
        conn.close()
        print("✅ Database connection successful")
    except Exception as e:
        error_str = str(e)
        print(f"❌ Database connection failed: {e}")
        print("")

        # Provide specific guidance based on error
        if "No such file or directory" in error_str and "/var/run/postgresql" in error_str:
            print("💡 This error means the DATABASE_URL is missing a host.")
            print("")
            print("   For Cloud Run with Cloud SQL, use one of these formats:")
            print("")
            print("   Option 1 - Public IP (simpler but less secure):")
            print("     DATABASE_URL=postgresql://USER:PASS@IP_ADDRESS:5432/DATABASE")
            print("     Example: postgresql://postgres:YOUR_PASSWORD@YOUR_IP:5432/postgres")
            print("")
            print("   Option 2 - Cloud SQL Connector (recommended):")
            print("     1. Add Cloud SQL connection in Cloud Run service settings")
            print("     2. Use: DATABASE_URL=postgresql://USER:PASS@/DATABASE?host=/cloudsql/PROJECT:REGION:INSTANCE")
            print("")
        elif "could not connect to server" in error_str or "Connection refused" in error_str:
            print("💡 Database server is unreachable. Check:")
            print("   - Is the IP address correct?")
            print("   - Is Cloud SQL instance running?")
            print("   - Are authorized networks configured? (Cloud SQL > Connections > Authorized networks)")
            print("")
        elif "password authentication failed" in error_str:
            print("💡 Wrong password. Check:")
            print("   - Password in DATABASE_URL matches Cloud SQL user password")
            print("   - Special characters are URL-encoded (& -> %26, = -> %3D, etc.)")
            print("")
        elif "database" in error_str and "does not exist" in error_str:
            print("💡 Database does not exist. Either:")
            print('   - Use the default "postgres" database')
            print("   - Create your database: CREATE DATABASE yourdb;")
            print("")

        sys.exit(1)


def check_schema_issues():
    """Check for known schema issues (report only, don't fail)."""
    print("🔍 Checking for known schema issues...")

    try:
        from src.core.database.db_config import get_db_connection

        conn = get_db_connection()
        issues = []

        # Check for commonly missing columns
        checks = [
            ("media_buys", "context_id"),
            ("creative_formats", "updated_at"),
        ]

        for table, column in checks:
            cursor = conn.execute(
                f"""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = '{table}' AND column_name = '{column}'
            """
            )
            if not cursor.fetchone():
                issues.append(f"Missing column: {table}.{column}")

        if issues:
            print("⚠️  Schema issues detected (non-critical):")
            for issue in issues:
                print(f"   - {issue}")
        else:
            print("✅ No known schema issues detected")

        conn.close()
    except Exception as e:
        print(f"⚠️  Could not check schema: {e}")


def init_database():
    """Initialize database schema and default data."""
    print("📦 Initializing database schema and default data...")
    print(
        "ℹ️  Note: init_db() is safe - it only creates tables (IF NOT EXISTS) and default tenant (if no tenants exist)"
    )

    try:
        from src.core.database.database import init_db

        init_db(exit_on_error=True)
        print("✅ Database initialization complete")
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        sys.exit(1)


def stop_services() -> None:
    """Stop every running service: ask all of them, wait one shared grace period, kill the rest."""
    running = [proc for proc in list(services.values()) if proc.poll() is None]
    for proc in running:
        proc.terminate()
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    for proc in running:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def cleanup(signum=None, frame=None):
    """Stop all services on a stop signal (SIGINT, SIGTERM) and exit.

    The exit code is 0 for a requested stop. It is 1 when the signal arrives while the
    launcher is already shutting down because a service failed.
    """
    print("\nShutting down all services...")
    stop_services()
    sys.exit(_exit_code)


def failed_service() -> str | None:
    """Which service failed, or ``None`` while every service the launcher was asked to run is running."""
    if start_failures:
        return start_failures[0]
    # A copy: the service threads register nginx and cron while the supervisor runs.
    for name, proc in list(services.items()):
        exit_code = proc.poll()
        if exit_code is not None:
            return f"{name} exited with code {exit_code}"
    return None


def supervise_services(seconds: float | None = None, poll_seconds: float = 1.0) -> None:
    """Watch the services; when one fails, stop the rest and exit with a failure code.

    A container whose app server has died still looks "running" to the container runtime
    while nginx answers 502 for every request, so the runtime's restart policy never fires.
    Exiting non-zero hands the decision back to it. Every service counts, cron included:
    without its publisher refresh, products stop being offered a day later.

    Returns after ``seconds`` when no service failed; with ``None`` it watches until the
    launcher is stopped.
    """
    global _exit_code
    deadline = None if seconds is None else time.monotonic() + seconds
    while deadline is None or time.monotonic() < deadline:
        failure = failed_service()
        if failure is not None:
            _exit_code = 1
            print(f"❌ {failure}. Stopping the other services.")
            stop_services()
            sys.exit(1)
        time.sleep(poll_seconds)


# Register cleanup handlers
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


def run_migrations():
    """Run database migrations before starting services."""
    print("📦 Running database migrations...")
    # Stream output live so a hang shows which migration is in flight (we have
    # 170 migrations; cold-DB runs blow past short timeouts and capture_output
    # makes them invisible to the operator).
    timeout_seconds = 300
    try:
        result = subprocess.run(
            [sys.executable, "scripts/ops/migrate.py"],
            timeout=timeout_seconds,
        )
        if result.returncode == 0:
            print("✅ Migrations complete")
        else:
            print(f"❌ Migration failed (exit code {result.returncode})")
            sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"❌ Migration timed out after {timeout_seconds} seconds")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Migration error: {e}")
        sys.exit(1)


def run_mcp_server():
    """Run the MCP server."""
    print("Starting MCP server on port 8080...")
    # nginx proxies to 8080 whatever ADCP_SALES_PORT says, so the port is passed as an
    # argument rather than by rewriting the child's environment.
    proc = subprocess.Popen(
        [sys.executable, "scripts/run_server.py", "--port", "8080"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    register_service("MCP server", proc)

    # Monitor the process output
    for line in iter(proc.stdout.readline, b""):
        if line:
            print(f"[MCP] {line.decode().rstrip()}")
    print("MCP server stopped")


def run_nginx():
    """Run nginx as reverse proxy."""
    print("Starting nginx reverse proxy on port 8000...")

    # Create nginx directories if they don't exist
    os.makedirs("/var/log/nginx", exist_ok=True)
    os.makedirs("/var/run", exist_ok=True)

    # Select nginx config based on ADCP_MULTI_TENANT env var
    # Default: simple (single-tenant, path-based routing only)
    # ADCP_MULTI_TENANT=true: full config with subdomain routing for multi-tenant
    runtime = get_settings().runtime
    if runtime.adcp_multi_tenant:
        config_path = "/etc/nginx/nginx-multi-tenant.conf"
        print("[Nginx] Using multi-tenant config (subdomain routing enabled)")

        # Multi-tenant config uses environment variable templates
        # Use envsubst to substitute ${SALES_AGENT_DOMAIN} with actual value
        sales_agent_domain = runtime.sales_agent_domain or ""
        if not sales_agent_domain:
            print("❌ SALES_AGENT_DOMAIN is required for multi-tenant mode")
            sys.exit(1)

        print(f"[Nginx] Substituting SALES_AGENT_DOMAIN={sales_agent_domain}")

        # Read template, substitute variables, write to active config
        with open(config_path) as f:
            config_content = f.read()

        # Simple string substitution for ${SALES_AGENT_DOMAIN}
        config_content = config_content.replace("${SALES_AGENT_DOMAIN}", sales_agent_domain)

        with open("/etc/nginx/nginx.conf", "w") as f:
            f.write(config_content)
    else:
        config_path = "/etc/nginx/nginx-single-tenant.conf"
        print("[Nginx] Using single-tenant config (path-based routing only)")

        # Copy selected config to active location
        import shutil

        shutil.copy(config_path, "/etc/nginx/nginx.conf")

    # Test nginx configuration first
    test_proc = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if test_proc.returncode != 0:
        print(f"❌ Nginx configuration test failed: {test_proc.stderr}")
        raise RuntimeError("nginx refused its configuration")
    print("✅ Nginx configuration test passed")

    # Start nginx
    proc = subprocess.Popen(
        ["nginx", "-g", "daemon off;"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    register_service("nginx", proc)

    # Monitor the process output
    for line in iter(proc.stdout.readline, b""):
        if line:
            print(f"[Nginx] {line.decode().rstrip()}")
    print("Nginx stopped")


def run_cron():
    """Run supercronic for scheduled tasks."""
    crontab_path = "/app/crontab"
    if not os.path.exists(crontab_path):
        print("[Cron] No crontab found, skipping scheduled tasks")
        return

    print("Starting supercronic for scheduled tasks...")

    proc = subprocess.Popen(
        ["supercronic", crontab_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    register_service("cron", proc)

    # Monitor the process output
    for line in iter(proc.stdout.readline, b""):
        if line:
            print(f"[Cron] {line.decode().rstrip()}")
    print("Supercronic stopped")


def main():
    """Main entry point to run all services."""
    print("🚀 Starting AdCP Sales Agent...")
    print("=" * 60)
    print("AdCP Sales Agent - Starting All Services")
    print("=" * 60)

    # Validate environment variables
    validate_required_env()

    # Check database connectivity
    check_database_health()

    # Run migrations
    run_migrations()

    # Check for schema issues (non-blocking)
    check_schema_issues()

    # Initialize database
    init_database()

    # Start services in threads
    # MCP Server thread (serves MCP, A2A, and Admin in a single FastAPI process)
    start_service_thread("MCP server", run_mcp_server)

    # A2A and Admin UI are now integrated into the MCP server process (src/app.py)

    # Cron thread for scheduled tasks (syncing GAM tenants, etc.)
    runtime = get_settings().runtime
    if not runtime.skip_cron:
        start_service_thread("cron", run_cron)

    # Skipping nginx is useful for docker-compose with separate services
    if not runtime.skip_nginx:
        # Give services more time to start before nginx
        print("⏳ Waiting for backend services to be ready before starting nginx...")
        supervise_services(seconds=10)

        # Nginx reverse proxy thread
        start_service_thread("nginx", run_nginx)

        print("\n✅ All services started with unified routing:")
        print("  - MCP Server: http://localhost:8000/mcp")
        print("  - Admin UI: http://localhost:8000/admin")
        print("  - A2A Server: http://localhost:8000/a2a")
        print("\nPress Ctrl+C to stop all services")
    else:
        print("\n✅ Services started (nginx skipped):")
        print("  - Unified server: http://localhost:8080")
        print("    - MCP: http://localhost:8080/mcp")
        print("    - A2A: http://localhost:8080/a2a")
        print("    - Admin: http://localhost:8080/admin")
        print("\nℹ️  Nginx reverse proxy skipped (SKIP_NGINX=true)")
        print("Press Ctrl+C to stop all services")

    # Runs until the container is stopped (cleanup, on SIGINT or SIGTERM), a service exits,
    # or a service fails to start.
    supervise_services()


if __name__ == "__main__":
    main()
