import os
from dotenv import load_dotenv

load_dotenv()

from config import settings


def main():
    print("Starting AI社員 Discord Bot...")
    print(f"Discord token loaded: {'YES' if settings.DISCORD_TOKEN else 'NO'}")
    # TODO: Discordクライアントの初期化と起動処理を実装します


if __name__ == "__main__":
    main()
