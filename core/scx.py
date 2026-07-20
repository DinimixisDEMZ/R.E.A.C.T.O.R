"""
Interacción con scxctl y sudo.
Maneja la comunicación con el sistema de schedulers y la autenticación.
"""

import subprocess
import json

from utils.helpers import RE_RUNNING, RE_JSON_ARRAY


class ScxManager:
    """Capa de abstracción para interactuar con scxctl y sudo."""

    def __init__(self, modo_desarrollador=False):
        self.modo_desarrollador = modo_desarrollador
        self._sim_sched = "scx_rusty"
        self._sim_modo = "auto"

    def scx_run(self, args, capture=True):
        """Ejecuta un comando scxctl. En modo desarrollador simula la salida."""
        if self.modo_desarrollador:
            if "list" in args:
                ficticios = ["scx_rusty", "scx_lavd", "scx_central", "scx_bpfland", "scx_ghost", "scx_dummy", "scx_mock"]
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps(ficticios), stderr="")
            if "get" in args:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=f'RUNNING {self._sim_sched} ({self._sim_modo})', stderr="")
            if "switch" in args or "start" in args:
                for i, a in enumerate(args):
                    if a == "-s" and i + 1 < len(args):
                        self._sim_sched = args[i + 1]
                    elif a == "-m" and i + 1 < len(args):
                        self._sim_modo = args[i + 1]
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="OK (Simulated)", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="OK (Simulated)", stderr="")
        return subprocess.run(args, capture_output=capture, text=True)

    def detener_todos(self):
        """Detiene todos los schedulers activos y mata procesos scx_."""
        self.ejecutar_con_sudo(["scxctl", "stop"])
        self.ejecutar_con_sudo(["pkill", "-9", "-f", "scx_"])

    def ejecutar_con_sudo(self, cmd_list):
        """Wrapper seguro: Intenta usar sudo con la sesión activa."""
        if self.modo_desarrollador:
            self.scx_run(cmd_list)
            return subprocess.CompletedProcess(args=cmd_list, returncode=0, stdout="OK (Simulated)", stderr="")
        
        check = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        if check.returncode == 0:
            full_cmd = ["sudo"] + cmd_list
            return subprocess.run(full_cmd, capture_output=True, text=True)
        else:
            return subprocess.CompletedProcess(args=cmd_list, returncode=1, stderr="Autenticación requerida. (Sesión expirada)")

    def obtener_estado(self):
        """Obtiene el scheduler y modo actualmente en ejecución.
        
        Returns:
            tuple: (scheduler_name, mode) o (None, None) si no hay nada activo.
        """
        res = self.scx_run(["scxctl", "get"])
        match = RE_RUNNING.search(res.stdout.strip())
        if match:
            sc = match.group(1)
            modo = match.group(2) or "auto"
            return sc, modo
        return None, None

    def obtener_lista(self, compatibles=None):
        """Obtiene la lista de schedulers disponibles.
        
        Args:
            compatibles: Lista de schedulers verificados (filtra si no es None).
            
        Returns:
            list: Nombres de schedulers disponibles.
        """
        rl = self.scx_run(["scxctl", "list"])
        try:
            match_json = RE_JSON_ARRAY.search(rl.stdout)
            if match_json:
                raw_nombres = json.loads(match_json.group())
                if compatibles is not None:
                    return [n for n in raw_nombres if n in compatibles]
                return raw_nombres
        except json.JSONDecodeError:
            pass
        return []

    def sudo_disponible(self):
        """Verifica si sudo tiene una sesión activa."""
        if self.modo_desarrollador:
            return True
        return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0

    def validar_sudo(self, pwd):
        """Valida la contraseña y refresca el timestamp de sudo.
        
        Returns:
            bool: True si la autenticación fue exitosa.
        """
        proc = subprocess.run(
            ["sudo", "-S", "-v"],
            input=f"{pwd}\n",
            capture_output=True,
            text=True
        )
        del pwd
        return proc.returncode == 0
