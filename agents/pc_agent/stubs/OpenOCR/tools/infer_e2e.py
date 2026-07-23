class OpenOCR:
    """Stub for github.com/Topdu/OpenOCR's OpenOCR class.

    PC-Agent's run.py does `from OpenOCR.tools.infer_e2e import OpenOCR`
    unconditionally at module scope, but only instantiates it inside select()
    (used for the rare 'Select (' text-drag action). The real package pulls in
    paddlex and has a cwd-relative import in its own __init__.py that breaks when
    used as an external package (needs both OpenOCR's own directory AND its parent
    on sys.path) — see agents/pc_agent/setup_notes.md.

    This repo defaults PC_AGENT_USE_PERCEPTION_INFO=0, so none of PC-Agent's OCR
    paths (including this one) run in normal use. This stub exists purely to
    satisfy the top-level import; it fails loudly, not silently, if a task ever
    triggers a real Select action.
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "PC-Agent emitted a 'Select (...)' action, which needs the real "
            "OpenOCR package (github.com/Topdu/OpenOCR) — this repo stubs it out "
            "by default (agents/pc_agent/stubs/OpenOCR). See setup_notes.md for "
            "how to install the real package if you need this action to work."
        )
