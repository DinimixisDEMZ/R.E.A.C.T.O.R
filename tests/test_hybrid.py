"""
Tests para core/hybrid.py — motor de benchmarking hyperfine.
"""

from core.hybrid import _a_microsegundos


class TestAMicrosegundos:
    def test_segundos(self):
        assert _a_microsegundos(1, 's') == 1_000_000

    def test_milisegundos(self):
        assert _a_microsegundos(1, 'ms') == 1_000

    def test_microsegundos(self):
        assert _a_microsegundos(500, 'us') == 500

    def test_unidad_desconocida(self):
        assert _a_microsegundos(100, 'ns') == 100

    def test_cero(self):
        assert _a_microsegundos(0, 's') == 0
