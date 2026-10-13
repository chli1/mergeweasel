"""Which variants a scenario runs, and which a marker skips. No Gitea needed."""
import fixtures


def test_the_end_to_end_layer_adds_upgrade():
    assert fixtures.variants(["image", "workflow_files"]) == ["built", "released", "upgrade"]
    assert fixtures.variants(["image", "run_pass"]) == ["built", "released"]


def test_a_marker_never_skips_built():
    assert fixtures.skip_reason("built", True, "not released") is None


def test_needs_new_image_skips_released_until_released():
    assert fixtures.MARKERS["released"] == "needs_new_image"
    assert fixtures.skip_reason("released", True, "not released") == "needs_new_image: not released"
    assert fixtures.skip_reason("released", True, None) is None


def test_breaks_old_workflows_skips_upgrade_until_released():
    assert fixtures.MARKERS["upgrade"] == "breaks_old_workflows"
    assert fixtures.skip_reason("upgrade", True, "not released") == "breaks_old_workflows: not released"
    assert fixtures.skip_reason("upgrade", True, None) is None


def test_an_unmarked_variant_runs():
    for variant in ("built", "released", "upgrade"):
        assert fixtures.skip_reason(variant, False, "not released") is None


def test_upgrade_without_a_revision_fails_even_when_marked():
    assert fixtures.missing_revision("upgrade", "") == (
        "the released image names no revision - [upgrade] cannot read its workflow files")
    assert fixtures.missing_revision("upgrade", "abc123") is None
    assert fixtures.missing_revision("released", "") is None
    assert fixtures.missing_revision("built", "") is None
