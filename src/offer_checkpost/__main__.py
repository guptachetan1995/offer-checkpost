import sys

from offer_checkpost.cli import main, settings

if __name__ == "__main__":
    sys.exit(main(environ=settings()))
