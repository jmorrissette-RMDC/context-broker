"""
Static analysis tests — engineering requirements from test plan §4.1 and §4.2.

These tests inspect source files directly using pathlib and standard Python.
No Docker, no running services, no network access required.
"""

import re
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture
def requirements_txt(project_root: Path) -> str:
    return (project_root / "requirements.txt").read_text(encoding="utf-8")


@pytest.fixture
def dockerfile(project_root: Path) -> str:
    return (project_root / "Dockerfile").read_text(encoding="utf-8")


@pytest.fixture
def compose(project_root: Path) -> dict:
    text = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


@pytest.fixture
def compose_text(project_root: Path) -> str:
    return (project_root / "docker-compose.yml").read_text(encoding="utf-8")


@pytest.fixture
def nginx_conf(project_root: Path) -> str:
    return (project_root / "nginx" / "nginx.conf").read_text(encoding="utf-8")


@pytest.fixture
def gitignore(project_root: Path) -> str:
    return (project_root / ".gitignore").read_text(encoding="utf-8")


# ===================================================================
# §4.1-1  Version pinning
# ===================================================================


class TestVersionPinning:
    """All dependencies and base images must be pinned to exact versions."""

    def test_requirements_all_pinned(self, requirements_txt: str):
        """Every non-blank, non-comment line in requirements.txt uses '=='."""
        for lineno, raw in enumerate(requirements_txt.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            assert (
                "==" in line
            ), f"requirements.txt line {lineno} is not pinned with '==': {line}"
            # Must not use loose specifiers alongside ==
            for op in (">=", "<=", "~=", "!="):
                assert (
                    op not in line
                ), f"requirements.txt line {lineno} uses '{op}' instead of strict '==': {line}"

    def test_dockerfile_from_pinned(self, dockerfile: str):
        """Every FROM directive uses a specific version tag, not :latest or bare."""
        from_lines = [
            ln.strip()
            for ln in dockerfile.splitlines()
            if ln.strip().upper().startswith("FROM ")
        ]
        assert from_lines, "Dockerfile has no FROM directive"

        for line in from_lines:
            # Ignore "FROM ... AS ..." alias portion
            image_ref = line.split()[1]
            assert (
                ":" in image_ref
            ), f"FROM uses untagged image (implicit :latest): {line}"
            tag = image_ref.split(":", 1)[1]
            assert tag.lower() != "latest", f"FROM explicitly uses :latest: {line}"


# ===================================================================
# §4.1-2  Credential files
# ===================================================================


class TestCredentialFiles:
    """Credential scaffolding is in place and secrets are git-ignored."""

    def test_env_example_exists(self, project_root: Path):
        """.env.example exists in config/credentials/."""
        env_example = project_root / "config" / "credentials" / ".env.example"
        assert env_example.is_file(), f"Expected credential template at {env_example}"

    def test_gitignore_excludes_env(self, gitignore: str):
        """.gitignore contains a rule for .env files."""
        lines = [ln.strip() for ln in gitignore.splitlines()]
        has_env_rule = any(".env" in ln and not ln.startswith("#") for ln in lines)
        assert has_env_rule, ".gitignore does not contain a .env exclusion rule"


# ===================================================================
# §4.1-3  OTS backing services
# ===================================================================


class TestOTSBackingServices:
    """Postgres, Neo4j, and Redis must use pre-built images, not local builds."""

    @pytest.mark.parametrize(
        "service_name",
        ["context-broker-postgres", "context-broker-neo4j", "context-broker-redis"],
    )
    def test_backing_service_uses_image(self, compose: dict, service_name: str):
        """Backing service uses 'image:' and not 'build:'."""
        svc = compose["services"][service_name]
        assert "image" in svc, f"{service_name} is missing 'image:' key"
        assert (
            "build" not in svc
        ), f"{service_name} should not use 'build:' — OTS services must use pre-built images"


# ===================================================================
# §4.1-4  USER directive
# ===================================================================


class TestDockerfileUserDirective:
    """Dockerfile drops root privileges via USER after useradd."""

    def test_user_after_useradd(self, dockerfile: str):
        useradd_line = None
        user_line = None

        for idx, raw in enumerate(dockerfile.splitlines()):
            line = raw.strip()
            if "useradd" in line:
                useradd_line = idx
            if re.match(r"^USER\s+", line) and useradd_line is not None:
                user_line = idx
                break

        assert useradd_line is not None, "Dockerfile does not contain useradd"
        assert user_line is not None, "Dockerfile has no USER directive after useradd"
        assert user_line > useradd_line, "USER directive must appear after useradd"


# ===================================================================
# §4.1-5  HEALTHCHECK
# ===================================================================


class TestDockerfileHealthcheck:
    """Dockerfile includes a HEALTHCHECK directive."""

    def test_healthcheck_present(self, dockerfile: str):
        has_healthcheck = any(
            line.strip().startswith("HEALTHCHECK") for line in dockerfile.splitlines()
        )
        assert has_healthcheck, "Dockerfile is missing a HEALTHCHECK directive"


# ===================================================================
# §4.1-6  Two-network topology
# ===================================================================


class TestTwoNetworkTopology:
    """Gateway sits on both default and context-broker-net.
    Internal services sit only on context-broker-net.
    Ollama sits on both (external model pulls + internal serving)."""

    def _get_networks(self, compose: dict, service_name: str) -> set[str]:
        svc = compose["services"][service_name]
        nets = svc.get("networks", [])
        if isinstance(nets, list):
            return set(nets)
        if isinstance(nets, dict):
            return set(nets.keys())
        return set()

    def test_gateway_on_both_networks(self, compose: dict):
        nets = self._get_networks(compose, "context-broker")
        assert "default" in nets, "Gateway must be on 'default' network"
        assert (
            "context-broker-net" in nets
        ), "Gateway must be on 'context-broker-net' network"

    @pytest.mark.parametrize(
        "service_name",
        [
            "context-broker-langgraph",
            "context-broker-postgres",
            "context-broker-neo4j",
            "context-broker-redis",
        ],
    )
    def test_internal_services_only_on_internal_net(
        self, compose: dict, service_name: str
    ):
        nets = self._get_networks(compose, service_name)
        assert nets == {
            "context-broker-net"
        }, f"{service_name} should only be on context-broker-net, got {nets}"

    def test_ollama_on_internal_network(self, compose: dict):
        """Ollama must be on context-broker-net for internal serving."""
        nets = self._get_networks(compose, "context-broker-ollama")
        assert "context-broker-net" in nets, "Ollama must be on context-broker-net"

    def test_internal_network_is_bridge(self, compose: dict):
        """context-broker-net must be a standard bridge (NOT internal:true).

        Containers need outbound internet via Docker NAT for cloud LLM APIs.
        """
        net_def = compose.get("networks", {}).get("context-broker-net", {})
        assert net_def.get("internal") is not True, (
            "context-broker-net must NOT be internal:true — containers need outbound internet"
        )
        assert net_def.get("driver", "bridge") == "bridge", (
            "context-broker-net must be a bridge network"
        )


# ===================================================================
# §4.1-7  Thin gateway
# ===================================================================


class TestThinGateway:
    """nginx.conf contains only routing directives — no application logic."""

    def test_no_lua_directives(self, nginx_conf: str):
        """No Lua scripting in the gateway."""
        lua_patterns = [
            r"\bcontent_by_lua",
            r"\baccess_by_lua",
            r"\brewrite_by_lua",
            r"\binit_by_lua",
            r"\bbalancer_by_lua",
            r"\blua_package_path",
        ]
        for pattern in lua_patterns:
            assert not re.search(
                pattern, nginx_conf
            ), f"nginx.conf contains Lua directive matching '{pattern}'"

    def test_no_application_logic(self, nginx_conf: str):
        """No embedded scripting, templating, or processing directives."""
        banned = [
            r"\bperl_",
            r"\bjs_",  # njs module
            r"\bsub_filter",
            r"\badd_header\s+X-Custom-Logic",
        ]
        for pattern in banned:
            assert not re.search(
                pattern, nginx_conf
            ), f"nginx.conf contains application-logic directive matching '{pattern}'"

    def test_has_proxy_pass(self, nginx_conf: str):
        """Gateway must actually proxy traffic (sanity check)."""
        assert (
            "proxy_pass" in nginx_conf
        ), "nginx.conf does not contain any proxy_pass directives"

    def test_has_location_blocks(self, nginx_conf: str):
        """Gateway must define location blocks for routing."""
        assert re.search(
            r"\blocation\s+", nginx_conf
        ), "nginx.conf does not contain any location blocks"


# ===================================================================
# §4.1-8  Service name DNS
# ===================================================================


class TestServiceNameDNS:
    """Environment variables in docker-compose.yml reference service names, not IPs."""

    _IP_PATTERN = re.compile(r"=\s*\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")

    def test_no_hardcoded_ips_in_environment(self, compose: dict):
        """No environment variable values contain raw IP addresses."""
        for svc_name, svc_def in compose.get("services", {}).items():
            env_list = svc_def.get("environment", [])
            if isinstance(env_list, dict):
                env_list = [f"{k}={v}" for k, v in env_list.items()]
            for entry in env_list:
                entry_str = str(entry)
                # Skip localhost references (127.0.0.1 / 0.0.0.0 are fine for
                # healthcheck binds, but _HOST vars should use service names)
                if "_HOST=" in entry_str:
                    assert not self._IP_PATTERN.search(entry_str), (
                        f"{svc_name}: environment var uses IP instead of "
                        f"service name DNS: {entry_str}"
                    )

    def test_host_vars_reference_service_names(self, compose: dict):
        """_HOST environment vars contain known service names."""
        known_service_names = set(compose.get("services", {}).keys())
        svc = compose["services"].get("context-broker-langgraph", {})
        env_list = svc.get("environment", [])
        if isinstance(env_list, dict):
            env_list = [f"{k}={v}" for k, v in env_list.items()]
        for entry in env_list:
            entry_str = str(entry)
            if "_HOST=" in entry_str:
                value = entry_str.split("=", 1)[1].strip()
                assert (
                    value in known_service_names
                ), f"_HOST value '{value}' is not a known docker-compose service name"


# ===================================================================
# §4.2-9  No hardcoded secrets
# ===================================================================


class TestNoHardcodedSecrets:
    """Codebase must not contain hardcoded credentials or API keys."""

    # Patterns that match actual values, not env-var references or placeholders.
    # The password pattern requires a quoted string value to avoid matching
    # Python keyword arguments like password=password (variable reference).
    _SECRET_PATTERNS = [
        # key=<actual-value> (not $, {, or empty)
        (r'(?i)api_key\s*=\s*["\'][A-Za-z0-9/+]{16,}', "api_key assignment"),
        (r'(?i)password\s*=\s*["\'][A-Za-z0-9/+]{8,}', "password assignment"),
        (r'(?i)secret\s*=\s*["\'][A-Za-z0-9/+]{16,}', "secret assignment"),
        # Bearer tokens
        (r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}", "bearer token"),
        # AWS-style keys
        (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    ]

    # Directories and files to skip
    _SKIP_DIRS = {
        "__pycache__",
        ".git",
        "data",
        ".pytest_cache",
        "node_modules",
        "docs",
        "tests",  # Test files may contain test credentials
    }
    _SKIP_SUFFIXES = {".pyc", ".pyo", ".egg-info", ".whl", ".tar.gz"}

    def _scan_files(self, project_root: Path):
        """Yield (path, lineno, line, description) for any secret matches."""
        for filepath in project_root.rglob("*"):
            if not filepath.is_file():
                continue
            if any(skip in filepath.parts for skip in self._SKIP_DIRS):
                continue
            if filepath.suffix in self._SKIP_SUFFIXES:
                continue
            # Only scan text-like files
            if filepath.suffix not in {
                ".py",
                ".yml",
                ".yaml",
                ".toml",
                ".cfg",
                ".ini",
                ".conf",
                ".sh",
                ".bash",
                ".env.example",
                ".txt",
                ".md",
                ".json",
                ".sql",
            }:
                continue
            try:
                text = filepath.read_text(encoding="utf-8", errors="ignore")
            except (OSError, PermissionError):
                continue

            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                # Skip comments
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                # Skip lines that are env-var references
                if re.search(r"\$\{?\w+\}?", line):
                    continue
                # Skip lines that use os.environ or os.getenv
                if "os.environ" in line or "os.getenv" in line or "getenv" in line:
                    continue
                # Skip lines with placeholder values
                if "changeme" in line.lower() or "CHANGE_ME" in line:
                    continue

                for pattern, desc in self._SECRET_PATTERNS:
                    if re.search(pattern, line):
                        yield filepath, lineno, line.strip(), desc

    def test_no_hardcoded_secrets(self, project_root: Path):
        """No source file contains hardcoded secret values."""
        violations = list(self._scan_files(project_root))
        if violations:
            report = "\n".join(
                f"  {fp.relative_to(project_root)}:{ln} [{desc}]: {text[:80]}"
                for fp, ln, text, desc in violations
            )
            pytest.fail(f"Hardcoded secrets detected:\n{report}")


# ===================================================================
# §4.2-10  StateGraph mandate — thin route handlers
# ===================================================================


class TestStateGraphMandate:
    """Route handlers delegate to StateGraph flows.
    They must not contain application logic beyond request parsing and flow invocation.
    """

    _ROUTE_DIR = PROJECT_ROOT / "app" / "routes"

    # Patterns indicating business logic that belongs in a flow, not a handler
    _FORBIDDEN_PATTERNS = [
        (r"\bSELECT\b", "raw SQL query"),
        (r"\bINSERT\b", "raw SQL query"),
        (r"\bUPDATE\b", "raw SQL query"),
        (r"\bDELETE\s+FROM\b", "raw SQL query"),
        (r"\.execute\s*\(", "direct DB execute"),
        (r"redis\.\w+\s*\(", "direct Redis call"),
        (r"neo4j.*session", "direct Neo4j session"),
        (r"ChatOpenAI|OllamaLLM|BaseLLM", "direct LLM instantiation"),
        (r"\.predict\s*\(|\.invoke\s*\((?!.*flow|.*_flow)", "direct model invocation"),
    ]

    @pytest.mark.parametrize(
        "route_file",
        ["health.py", "mcp.py", "chat.py", "metrics.py"],
    )
    def test_no_business_logic_in_handler(self, route_file: str):
        """Route handler file must not contain direct DB, Redis, Neo4j, or LLM calls."""
        filepath = self._ROUTE_DIR / route_file
        assert filepath.is_file(), f"Route file not found: {filepath}"
        text = filepath.read_text(encoding="utf-8")

        violations = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if '"""' in stripped or "'''" in stripped:
                continue
            for pattern, desc in self._FORBIDDEN_PATTERNS:
                if re.search(pattern, line):
                    violations.append((lineno, desc, stripped[:80]))

        if violations:
            report = "\n".join(
                f"  {route_file}:{ln} [{desc}]: {text}" for ln, desc, text in violations
            )
            pytest.fail(
                f"Route handler contains application logic that belongs in a StateGraph:\n{report}"
            )

    @pytest.mark.parametrize(
        "route_file",
        ["health.py", "mcp.py", "chat.py", "metrics.py"],
    )
    def test_handler_imports_a_flow(self, route_file: str):
        """Each route handler must delegate to StateGraph flows (not inline logic)."""
        filepath = self._ROUTE_DIR / route_file
        text = filepath.read_text(encoding="utf-8")
        # Route handlers must import from app.flows (direct) or app.stategraph_registry
        # (dynamic loading via REQ-001 §10). Either pattern satisfies delegation.
        assert re.search(
            r"from app\.(flows\.|stategraph_registry)", text
        ), f"{route_file} does not import any StateGraph flow or registry"


# ===================================================================
# §4.2-11  Structured logging
# ===================================================================


class TestStructuredLogging:
    """logging_setup.py must use a JSON formatter."""

    def test_json_formatter_used(self, project_root: Path):
        filepath = project_root / "app" / "logging_setup.py"
        assert filepath.is_file(), "app/logging_setup.py not found"
        text = filepath.read_text(encoding="utf-8")

        assert (
            "JsonFormatter" in text or "json" in text.lower()
        ), "logging_setup.py does not reference JSON formatting"

    def test_json_formatter_class_defined(self, project_root: Path):
        """A JsonFormatter class is defined (not just imported)."""
        filepath = project_root / "app" / "logging_setup.py"
        text = filepath.read_text(encoding="utf-8")
        assert re.search(
            r"class\s+JsonFormatter", text
        ), "logging_setup.py does not define a JsonFormatter class"

    def test_json_dumps_in_formatter(self, project_root: Path):
        """The formatter serialises log records with json.dumps."""
        filepath = project_root / "app" / "logging_setup.py"
        text = filepath.read_text(encoding="utf-8")
        assert (
            "json.dumps" in text
        ), "logging_setup.py does not call json.dumps — logs may not be structured JSON"


# ===================================================================
# §4.2-12  Build type registry — three build types
# ===================================================================


class TestBuildTypeRegistry:
    """Three build types must be registered: passthrough, standard-tiered, knowledge-enriched."""

    _EXPECTED_BUILD_TYPES = {"passthrough", "standard-tiered", "knowledge-enriched"}

    def test_build_type_modules_exist(self, project_root: Path):
        """Each build type has a corresponding module in the AE package."""
        bt_dir = project_root / "packages" / "context-broker-ae" / "src" / "context_broker_ae" / "build_types"
        assert bt_dir.is_dir(), "AE package build_types/ directory not found"

        expected_modules = {
            "passthrough.py",
            "standard_tiered.py",
            "knowledge_enriched.py",
        }
        actual_modules = {
            f.name
            for f in bt_dir.iterdir()
            if f.suffix == ".py" and f.name != "__init__.py"
        }

        missing = expected_modules - actual_modules
        assert not missing, f"Missing build type modules in AE package: {missing}"

    def test_build_types_registered_via_entry_points(self):
        """AE package registers build types via entry_points (REQ-001 §10)."""
        from importlib.metadata import entry_points

        ae_eps = entry_points(group="context_broker.ae")
        assert len(list(ae_eps)) > 0, (
            "No AE entry points found — context-broker-ae package not installed"
        )

    def test_ae_register_provides_build_types(self):
        """AE register() returns all three build types."""
        from context_broker_ae.register import register

        registration = register()
        assert "build_types" in registration, "register() must return build_types"
        for bt_name in self._EXPECTED_BUILD_TYPES:
            assert bt_name in registration["build_types"], (
                f"Build type '{bt_name}' not in AE registration"
            )
            asm, ret = registration["build_types"][bt_name]
            assert callable(asm), f"{bt_name} assembly builder not callable"
            assert callable(ret), f"{bt_name} retrieval builder not callable"
