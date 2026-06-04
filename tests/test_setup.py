"""Test to verify project setup."""
import sys

def test_python_version():
    """Ensure we're using Python 3.12. My Mac defaults to v2.7"""
    assert sys.version_info.major == 3
    assert sys.version_info.minor == 12
    
def test_import():
    """Test that the package can be imported."""
    import nexus
    assert nexus.__version__ == "0.1.0"  # Fixed: __version__ not version
