"""Compatibility guards for the Node-based LTSM bridge."""

from pathlib import Path


def test_bridge_installs_webcrypto_global_before_loading_sandbox():
    source = (
        Path(__file__).parents[1] / "okline" / "ltsm" / "ltsm_bridge.js"
    ).read_text(encoding="utf-8")
    fallback = "setGlobal('crypto', CRYPTO, true);"
    sandbox_load = "require(path.join(DIR, 'ltsmSandbox.js'));"

    assert fallback in source
    assert source.index(fallback) < source.index(sandbox_load)
