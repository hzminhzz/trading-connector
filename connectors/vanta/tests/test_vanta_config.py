import pytest

from connectors.vanta import VantaConfig
from connectors.vanta.tests.fixtures import ACCOUNT, KEY_ID, SECRET


def test_config_requires_explicit_values_and_exposes_fixed_base_url():
    config = VantaConfig(ACCOUNT, KEY_ID, SECRET)
    assert config.account_id == ACCOUNT
    assert config.api_key == KEY_ID
    assert config.api_secret == SECRET
    assert config.base_url == "https://app.vantatrading.io"


def test_config_repr_never_contains_credentials():
    text = repr(VantaConfig(ACCOUNT, KEY_ID, SECRET))
    assert SECRET not in text
    assert KEY_ID not in text


@pytest.mark.parametrize(
    "args",
    [
        ("", KEY_ID, SECRET),
        (ACCOUNT, "", SECRET),
        (ACCOUNT, KEY_ID, ""),
        (ACCOUNT, " " + KEY_ID, SECRET),
        (ACCOUNT, KEY_ID, SECRET + "\n"),
        (ACCOUNT, "key_without_prefix", SECRET),
        (ACCOUNT, KEY_ID, "secret_without_prefix"),
        (ACCOUNT, "vk_kéy", SECRET),
        (ACCOUNT, KEY_ID, "vks_sécret"),
    ],
)
def test_config_rejects_empty_whitespace_non_ascii_or_bad_prefix(args):
    with pytest.raises(ValueError):
        VantaConfig(*args)
