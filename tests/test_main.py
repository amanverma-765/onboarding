import pytest

from onboarding import main


def test_main_prints(capsys: pytest.CaptureFixture[str]) -> None:
    main.main()
    assert capsys.readouterr().out == "Hello World\n"
