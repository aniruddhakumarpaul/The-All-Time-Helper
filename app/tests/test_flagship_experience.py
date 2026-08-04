import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch


class FlagshipExperienceTests(unittest.TestCase):
    def test_response_policy_applies_style_and_safety_contract(self):
        from app.logic.response_policy import build_agent_quality_contract, build_assistant_system_prompt

        prompt = build_assistant_system_prompt(
            {"response_style": "deep", "english": True, "pers": True, "oneword": True}
        )
        contract = build_agent_quality_contract({"response_style": "creative"})

        self.assertIn("thorough answer with assumptions, tradeoffs", prompt)
        self.assertIn("Respond only in English", prompt)
        self.assertIn("never fabricate familiarity", prompt)
        self.assertIn("exactly one word", prompt)
        self.assertIn("Never claim that a tool action succeeded", contract)
        self.assertIn("Prioritize original concepts", contract)

    def test_helper_auto_prefers_healthy_cloud_and_falls_back_local(self):
        from app.logic import agents

        with (
            patch.object(agents, "_get_cloud_api_key", return_value="configured"),
            patch.object(agents, "_cloud_runtime_available", return_value=True),
        ):
            self.assertEqual(agents._resolve_auto_model("helper-auto"), "agentic-pro")

        with (
            patch.object(agents, "_get_cloud_api_key", return_value="configured"),
            patch.object(agents, "_cloud_runtime_available", return_value=False),
        ):
            self.assertEqual(agents._resolve_auto_model("helper-auto"), "gemma4:e2b")

        with patch.object(agents, "_get_cloud_api_key", side_effect=ValueError("missing")):
            self.assertEqual(agents._resolve_auto_model("helper-auto"), "gemma4:e2b")

        self.assertEqual(agents._resolve_auto_model("gemma2:2b"), "gemma2:2b")

    def test_helper_auto_recovers_from_cloud_transport_failure(self):
        from app.logic import agents

        statuses = []
        intent = {"is_sensitive": False, "requires_tools": False, "complexity": "direct", "is_local": False}
        context = {"final_prompt": "runtime check", "memory_block": "", "history_context": ""}
        with (
            patch.object(agents, "_resolve_auto_model", return_value="agentic-pro"),
            patch.object(agents, "_resolve_visual_task_continuation", return_value=None),
            patch.object(agents, "_try_direct_tool_execution", return_value=None),
            patch.object(agents, "_assemble_context", return_value=context),
            patch.object(agents, "_execute_cloud", return_value="Cloud Engine Error: provider unavailable"),
            patch.object(agents, "_execute_local", return_value="LOCAL_RECOVERY_OK") as local,
            patch.object(agents, "_mark_cloud_runtime_failure") as mark_failure,
            patch.object(agents, "_harden_result", side_effect=lambda result, *args, **kwargs: result),
        ):
            result = agents.run_helper_agent(
                "runtime check",
                target_model="helper-auto",
                history=[],
                intent=intent,
                status_callback=statuses.append,
                chunk_callback=lambda _chunk: None,
            )

        self.assertEqual(result, "LOCAL_RECOVERY_OK")
        mark_failure.assert_called_once_with("agentic-pro", reason="provider_unavailable")
        local.assert_called_once()
        self.assertEqual(local.call_args.args[2], "gemma2:2b")
        self.assertFalse(local.call_args.kwargs["allow_cloud_fallback"])
        self.assertIn("Cloud route unavailable. Switching to the private local assistant...", statuses)

    def test_cloud_runtime_circuit_reports_degraded_without_exposing_errors(self):
        from app.logic import agent_model_registry as registry

        registry.reset_cloud_runtime_state()
        try:
            with patch.object(registry, "has_cloud_credentials", return_value=True):
                registry.mark_cloud_runtime_failure(cooldown_seconds=60)
                status = registry.cloud_runtime_status()
            self.assertTrue(status["configured"])
            self.assertFalse(status["available"])
            self.assertTrue(status["degraded"])
            self.assertGreater(status["retry_after_seconds"], 0)
            self.assertEqual(status["reason"], "provider_unavailable")
            self.assertEqual(
                set(status),
                {"configured", "available", "degraded", "retry_after_seconds", "reason"},
            )
        finally:
            registry.reset_cloud_runtime_state()

    def test_cloud_failures_have_sanitized_operational_categories(self):
        from app.logic import agent_cloud, agents

        network_error = RuntimeError(
            "ProxyError while requesting /prompt/private-user-description through 127.0.0.1"
        )
        self.assertEqual(agent_cloud._provider_error_category(network_error), "network_unavailable")
        self.assertEqual(
            agent_cloud._provider_error_message("network_unavailable"),
            "The cloud provider could not be reached.",
        )
        self.assertNotIn(
            "private-user-description",
            agent_cloud._provider_error_message("network_unavailable"),
        )
        self.assertEqual(
            agents._cloud_failure_reason("Cloud Engine Error: The cloud provider could not be reached."),
            "network_unavailable",
        )
        self.assertEqual(
            agents._cloud_failure_reason("Cloud Engine Error: temporarily rate limited"),
            "rate_limited",
        )
    def test_obvious_intents_skip_llm_classification(self):
        from app.logic import agents

        with patch.object(
            agents,
            "_analyze_prompt_via_llm",
            side_effect=AssertionError("obvious intent should not call an LLM classifier"),
        ):
            direct = agents._detect_intent(
                "Reply with exactly ROUTE_OK and nothing else.", "agentic-pro", history=[]
            )
            search = agents._detect_intent(
                "Search the web for official Python documentation.", "gemma2:2b", history=[]
            )
            email = agents._detect_intent(
                "Draft an email to test@example.com saying hello.", "agentic-pro", history=[]
            )

        self.assertEqual(direct["complexity"], "direct")
        self.assertFalse(direct["requires_tools"])
        self.assertEqual(search["complexity"], "single")
        self.assertTrue(search["requires_tools"])
        self.assertTrue(search["is_local"])
        self.assertEqual(email["complexity"], "single")
        self.assertTrue(email["requires_tools"])
        self.assertFalse(email["is_local"])

    def test_explicit_email_draft_uses_deterministic_tool_contract(self):
        from app.logic import agents

        statuses = []
        result = agents._try_direct_tool_execution(
            "Draft an email to test@example.com with subject Runtime check and body Tool path works. Do not send it.",
            {"is_local": False, "requires_tools": True, "complexity": "single"},
            [],
            target_model="agentic-pro",
            status_callback=statuses.append,
        )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        draft = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(draft["recipient"], "test@example.com")
        self.assertEqual(draft["subject"], "Runtime check")
        self.assertEqual(draft["body"], "Tool path works.")
        self.assertIn("Preparing a validated email draft...", statuses)

    def test_search_uses_bundled_ca_and_formats_sources(self):
        from app.logic import tools

        with (
            patch("ddgs.DDGS") as ddgs_class,
            patch.object(tools.certifi, "where", return_value="C:/trusted/cacert.pem"),
            patch.dict(tools.os.environ, {}, clear=True),
        ):
            tools._ddgs_client()
            configured_ca = {
                name: tools.os.environ.get(name)
                for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
            }

        ddgs_class.assert_called_once_with(timeout=12, verify="C:/trusted/cacert.pem")
        self.assertEqual(set(configured_ca.values()), {"C:/trusted/cacert.pem"})

        with patch.object(tools, "_ddgs_client") as client_factory:
            client_factory.return_value.text.return_value = [
                {
                    "title": "Python documentation",
                    "body": "Official language documentation",
                    "href": "https://docs.python.org/3.12/",
                }
            ]
            result = tools.search_tool.func(query="Python 3.12 documentation")

        client_factory.return_value.text.assert_called_once_with(
            "Python 3.12 documentation", backend="brave", max_results=5
        )
        self.assertIn("Title: Python documentation", result)
        self.assertIn("URL: https://docs.python.org/3.12/", result)

    def test_search_falls_through_ordered_providers(self):
        from app.logic import tools

        client = unittest.mock.Mock()
        client.text.side_effect = [RuntimeError("provider down"), [], [{"title": "Result"}]]

        results = tools._ddgs_search(
            client,
            "text",
            "runtime verification",
            ("brave", "yahoo", "yandex", "bing"),
            5,
        )

        self.assertEqual(results, [{"title": "Result"}])
        self.assertEqual(
            [call.kwargs["backend"] for call in client.text.call_args_list],
            ["brave", "yahoo", "yandex"],
        )

    def test_openrouter_free_chain_uses_verified_gemma_models(self):
        from app.logic.agent_model_registry import FREE_AGENT_FALLBACKS, FREE_AGENT_PRIMARY

        self.assertEqual(FREE_AGENT_PRIMARY, "openrouter/google/gemma-4-26b-a4b-it:free")
        self.assertEqual(
            FREE_AGENT_FALLBACKS,
            ("openrouter/google/gemma-4-31b-it:free",),
        )
        chain = " ".join((FREE_AGENT_PRIMARY, *FREE_AGENT_FALLBACKS))
        self.assertNotIn("nemotron-nano-9b-v2", chain)
        self.assertNotIn("north-mini-code", chain)

    def test_openrouter_grounded_search_requires_verifiable_citations(self):
        from app.logic import tools

        response = unittest.mock.Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {
                "content": "Python 3.12 documentation is available from Python.org.",
                "annotations": [{
                    "type": "url_citation",
                    "url_citation": {
                        "title": "Python 3.12 documentation",
                        "url": "https://docs.python.org/3.12/",
                    },
                }],
            }}]
        }
        with (
            patch(
                "app.logic.agent_model_registry.get_cloud_api_key",
                return_value="configured-openrouter-key",
            ),
            patch.object(tools.requests, "post", return_value=response) as request,
        ):
            result = tools._openrouter_grounded_search("official Python 3.12 documentation")

        self.assertIn("Python 3.12 documentation", result)
        self.assertIn("https://docs.python.org/3.12/", result)
        sent = request.call_args.kwargs["json"]
        self.assertEqual(sent["model"], "google/gemma-4-26b-a4b-it:free")
        self.assertEqual(
            sent["tools"],
            [{"type": "openrouter:web_search", "engine": "auto", "max_total_results": 3}],
        )

    def test_search_uses_openrouter_only_after_anonymous_providers_fail(self):
        from app.logic import tools

        with (
            patch.object(tools, "_ddgs_client") as client_factory,
            patch.object(
                tools,
                "_openrouter_grounded_search",
                return_value="Grounded answer\n\nSources:\n- [Source](https://example.com)",
            ) as grounded,
        ):
            client_factory.return_value.text.return_value = []
            result = tools.search_tool.func(query="runtime verification")

        grounded.assert_called_once_with("runtime verification")
        self.assertIn("https://example.com", result)

    def test_explicit_search_executes_directly_on_local_and_cloud_routes(self):
        from app.logic import agents

        with patch.object(
            agents.tools.search_tool,
            "func",
            return_value="Title: Python documentation\nURL: https://docs.python.org/3.12/",
        ) as search:
            local_result = agents._try_direct_tool_execution(
                "Search the web for official Python documentation.",
                {"is_local": True, "requires_tools": True, "complexity": "single"},
                [],
                target_model="gemma4:e2b",
            )
            cloud_result = agents._try_direct_tool_execution(
                "Search the web for official Python documentation.",
                {"is_local": False, "requires_tools": True, "complexity": "single"},
                [],
                target_model="agentic-pro",
            )

        self.assertEqual(search.call_count, 2)
        self.assertEqual(
            [call.kwargs["query"] for call in search.call_args_list],
            ["official Python documentation", "official Python documentation"],
        )
        self.assertIn("https://docs.python.org/3.12/", local_result)
        self.assertIn("https://docs.python.org/3.12/", cloud_result)

    def test_agent_dependencies_use_bundled_metadata_and_disable_telemetry(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "app" / "logic" / "agents.py").read_text(encoding="utf-8")
        crew_import = source.index("from crewai import")
        for setting in (
            "LITELLM_LOCAL_MODEL_COST_MAP",
            "CREWAI_DISABLE_TELEMETRY",
            "OTEL_SDK_DISABLED",
            "ANONYMIZED_TELEMETRY",
            "CREWAI_STORAGE_DIR",
        ):
            self.assertLess(source.index(setting), crew_import)

        budget_source = (root / "app" / "logic" / "cloud_token_budget.py").read_text(encoding="utf-8")
        litellm_import = budget_source.index("import litellm")
        for setting in (
            "LITELLM_LOCAL_MODEL_COST_MAP",
            "CREWAI_DISABLE_TELEMETRY",
            "OTEL_SDK_DISABLED",
            "ANONYMIZED_TELEMETRY",
            "CREWAI_STORAGE_DIR",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
        ):
            self.assertLess(budget_source.index(setting), litellm_import)

    def test_runtime_status_reports_hybrid_and_offline_modes(self):
        from app.routes import health

        with (
            patch.object(health.requests, "get", return_value=object()),
            patch.object(health, "cloud_runtime_status", return_value={
                "configured": True, "available": True, "degraded": False, "retry_after_seconds": 0
            }),
        ):
            hybrid = asyncio.run(health.get_status())

        with (
            patch.object(health.requests, "get", side_effect=RuntimeError("offline")),
            patch.object(health, "cloud_runtime_status", return_value={
                "configured": False, "available": False, "degraded": False, "retry_after_seconds": 0
            }),
        ):
            offline = asyncio.run(health.get_status())

        self.assertEqual(hybrid["mode"], "hybrid")
        self.assertTrue(hybrid["running"])
        self.assertTrue(hybrid["cloud_configured"])
        self.assertTrue(hybrid["cloud_available"])
        self.assertIn("research", hybrid["capabilities"])
        self.assertEqual(offline["mode"], "offline")
        self.assertFalse(offline["running"])
        self.assertFalse(offline["cloud_configured"])

    def test_template_restores_original_interface_with_current_runtime_hooks(self):
        root = Path(__file__).resolve().parents[2]
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        particles = (root / "static" / "js" / "particles.js").read_text(encoding="utf-8")

        self.assertIn('/static/css/style_v3.css?v=146', template)
        self.assertNotIn('flagship.css', template)
        self.assertNotIn('experience.js', template)
        self.assertIn('id="particle-canvas"', template)
        self.assertIn('id="center-greet"', template)
        self.assertIn('How can I help you today?', template)
        self.assertIn('data-model-id="helper-auto"', template)
        self.assertIn('<span id="active-model-name">Helper Auto</span>', template)
        self.assertIn('data-model-name="Gemma 4 Cloud (Free)"', template)
        self.assertIn('data-model-name="Cloud Code Partner (Free)"', template)
        self.assertNotIn('North Mini Code Free', template)
        self.assertNotIn('Nemotron Nano Free', template)
        self.assertIn('/static/js/particles.js?v=146', template)
        self.assertIn('/static/js/app.js?v=223', template)
        self.assertIn("window.helperApiUrl", bootstrap)
        self.assertIn("requestAnimationFrame", particles)

    def test_original_brand_and_type_contract_is_active(self):
        root = Path(__file__).resolve().parents[2]
        style = (root / "static" / "css" / "style_v3.css").read_text(encoding="utf-8")

        self.assertIn("family=Outfit", style)
        self.assertIn("--logo-grad", style)
        self.assertIn("--greet-grad", style)
        self.assertIn(".logo-text", style)


if __name__ == "__main__":
    unittest.main()
