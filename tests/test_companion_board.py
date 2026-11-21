import pytest
from core.companion_board import load_board,save_board


def test_board_roundtrip_atomic_and_bad_write_preserves_previous(tmp_path):
    path=tmp_path/"board.json";good={"schema":1,"points":[[.1,.2],[1,0]]}
    save_board(good,path)
    assert load_board(path)==good
    with pytest.raises(ValueError): save_board({"schema":1,"points":[[float("nan"),0]]},path)
    assert load_board(path)==good


@pytest.mark.parametrize("value",[{"schema":1,"points":[[0,0]]*513},{"schema":1,"points":[[2,0]]},{"schema":1,"points":[],"script":"bad"}])
def test_board_rejects_unbounded_or_unknown_fields(value,tmp_path):
    with pytest.raises(ValueError):save_board(value,tmp_path/"board.json")
