import ast
import re
from pathlib import Path

import pytest


def phone_pattern():
    # Test the production regex without loading the unrelated NLP model.
    module = ast.parse(Path('app/stage0_extraction/pii_masker.py').read_text())
    node = next(node for node in module.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'PHONE_PATTERN'
                        for target in node.targets))
    return re.compile(ast.literal_eval(node.value.args[0]), re.IGNORECASE)


@pytest.mark.parametrize('phone', [
    '212-555-0199x1234', '(212)555-0199x123', '212.555.0199x12345',
    '+1-212-555-0199x123', '001-212-555-0199x123',
    '212-555-0199 ext. 123', '+977-9812345678', '9812345678',
])
def test_phone_and_extension_fully_removed(phone):
    assert phone_pattern().sub('[REDACTED_PHONE]', phone) == '[REDACTED_PHONE]'


def test_work_dates_and_skill_numbers_preserved():
    value = 'Java 17; Python 3; experience 2019-2024; 5 years'
    assert phone_pattern().sub('[REDACTED_PHONE]', value) == value
