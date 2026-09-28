import offer_checkpost


def test_package_imports_with_a_version():
    assert offer_checkpost.__version__ == "0.1.0"
