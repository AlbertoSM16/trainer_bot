"""Inicia sesión en Garmin Connect una vez y guarda los tokens para el bot.

    .venv/bin/python garmin_login.py

Pide el correo, la contraseña y, si tu cuenta lo tiene, el código MFA. Los tokens se guardan
en GARMIN_TOKENS_DIR (por defecto ~/.garminconnect) y duran meses; la contraseña no se guarda.
"""
import getpass
import os

from config import GARMIN_EMAIL, GARMIN_TOKENS_DIR


def main():
    from garminconnect import Garmin

    email = GARMIN_EMAIL or input("Correo de Garmin Connect: ").strip()
    password = getpass.getpass("Contraseña: ")
    g = Garmin(email, password, prompt_mfa=lambda: input("Código MFA: ").strip())
    os.makedirs(GARMIN_TOKENS_DIR, exist_ok=True)
    os.chmod(GARMIN_TOKENS_DIR, 0o700)
    g.login(GARMIN_TOKENS_DIR)
    g.client.dump(GARMIN_TOKENS_DIR)
    print(f"✅ Sesión iniciada como {g.get_full_name() or email}.")
    print(f"Tokens guardados en {GARMIN_TOKENS_DIR}. Pon GARMIN_CHAT_ID en .env y arranca el bot.")


if __name__ == "__main__":
    main()
