from issue_flow.state import StateDir


def test_state_roundtrip(tmp_path):
    s = StateDir(tmp_path, 42)
    assert s.read("state") == {}
    s.write("state", {"a": 1, "b": [1, 2]})
    assert s.read("state") == {"a": 1, "b": [1, 2]}
    assert (tmp_path / ".specify/issue-flow/42/state.json").is_file()


def test_nested_name_creates_dirs(tmp_path):
    s = StateDir(tmp_path, 7)
    s.write("stories/12/state", {"x": 1})
    assert (tmp_path / ".specify/issue-flow/7/stories/12/state.json").is_file()
    assert s.read("stories/12/state") == {"x": 1}
