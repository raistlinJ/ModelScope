"""
Unit tests for core.schema_registry — SchemaRegistry static methods.
"""
import json
import pytest
from pathlib import Path
from core.schema_registry import SchemaRegistry


# ── parse_schema_from_json ────────────────────────────────────────────────────

class TestParseSchemaFromJson:
    def test_valid_json_parses(self):
        schema_json = '{"type": "object", "properties": {"path": {"type": "string"}}}'
        result = SchemaRegistry.parse_schema_from_json(schema_json)
        assert result["type"] == "object"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            SchemaRegistry.parse_schema_from_json("{not valid}")


# ── save_to_registry / load_from_registry ────────────────────────────────────

class TestSaveAndLoad:
    def test_save_creates_file(self, tmp_path):
        schema = {"type": "object", "properties": {}}
        path = SchemaRegistry.save_to_registry("my_tool", schema, str(tmp_path))
        assert Path(path).exists()
        assert path.endswith("my_tool.json")

    def test_load_returns_saved_schema(self, tmp_path):
        schema = {"type": "object", "properties": {"target": {"type": "string"}}}
        SchemaRegistry.save_to_registry("nmap", schema, str(tmp_path))
        loaded = SchemaRegistry.load_from_registry("nmap", str(tmp_path))
        assert loaded == schema

    def test_load_nonexistent_returns_none(self, tmp_path):
        result = SchemaRegistry.load_from_registry("nonexistent", str(tmp_path))
        assert result is None

    def test_save_creates_parent_dirs(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "c")
        SchemaRegistry.save_to_registry("tool", {}, nested)
        assert Path(nested).is_dir()


# ── list_registry_tools ───────────────────────────────────────────────────────

class TestListRegistryTools:
    def test_empty_dir_returns_empty_list(self, tmp_path):
        result = SchemaRegistry.list_registry_tools(str(tmp_path))
        assert result == []

    def test_lists_only_json_files(self, tmp_path):
        (tmp_path / "tool_a.json").write_text("{}")
        (tmp_path / "tool_b.json").write_text("{}")
        (tmp_path / "notes.txt").write_text("not a schema")
        result = SchemaRegistry.list_registry_tools(str(tmp_path))
        assert set(result) == {"tool_a", "tool_b"}

    def test_nonexistent_dir_returns_empty_list(self, tmp_path):
        result = SchemaRegistry.list_registry_tools(str(tmp_path / "does_not_exist"))
        assert result == []


# ── generate_metrics_from_schema ─────────────────────────────────────────────

class TestGenerateMetricsFromSchema:
    def test_returns_at_least_one_metric(self):
        schema = {"type": "object", "properties": {}}
        metrics = SchemaRegistry.generate_metrics_from_schema("my_tool", schema)
        assert len(metrics) >= 1

    def test_required_arg_gets_metric(self):
        schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        }
        metrics = SchemaRegistry.generate_metrics_from_schema("file_creator", schema)
        names = [m["name"] for m in metrics]
        assert any("path" in n for n in names)

    def test_enum_arg_gets_content_contains_metric(self):
        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["read", "write"]},
            },
        }
        metrics = SchemaRegistry.generate_metrics_from_schema("file_tool", schema)
        types = [m["type"] for m in metrics]
        assert "content_contains" in types

    def test_pattern_arg_gets_content_regex_metric(self):
        schema = {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "pattern": r"^\d+\.\d+\.\d+\.\d+$"},
            },
        }
        metrics = SchemaRegistry.generate_metrics_from_schema("scan", schema)
        types = [m["type"] for m in metrics]
        assert "content_regex" in types

    def test_return_object_schema_adds_metric(self):
        schema = {
            "type": "object",
            "properties": {},
            "returns": {"type": "object"},
        }
        metrics = SchemaRegistry.generate_metrics_from_schema("my_tool", schema)
        # Should include the return schema metric
        assert any("return schema" in m["name"].lower() for m in metrics)

    def test_metric_ids_are_unique(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "string", "enum": ["x"]},
                "b": {"type": "string", "pattern": r"\d+"},
            },
            "required": ["a", "b"],
        }
        metrics = SchemaRegistry.generate_metrics_from_schema("tool", schema)
        ids = [m["id"] for m in metrics]
        assert len(ids) == len(set(ids))

    def test_all_metrics_enabled_by_default(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        metrics = SchemaRegistry.generate_metrics_from_schema("tool", schema)
        assert all(m.get("enabled") is True for m in metrics)


# ── generate_preset_metrics_for_category ─────────────────────────────────────

class TestPresetMetrics:
    @pytest.mark.parametrize("category", [
        "web_search", "code_execution", "database_query",
        "calendar_email", "file_system",
    ])
    def test_known_category_returns_non_empty(self, category):
        metrics = SchemaRegistry.generate_preset_metrics_for_category(category)
        assert len(metrics) > 0

    def test_unknown_category_returns_empty(self):
        metrics = SchemaRegistry.generate_preset_metrics_for_category("totally_fake")
        assert metrics == []

    @pytest.mark.parametrize("category", ["web_search", "code_execution"])
    def test_preset_metrics_have_required_fields(self, category):
        metrics = SchemaRegistry.generate_preset_metrics_for_category(category)
        for m in metrics:
            assert "id" in m
            assert "type" in m
            assert "enabled" in m
