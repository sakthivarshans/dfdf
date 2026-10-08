from services.ocr_service import ocr_service

def main():

    print("=" * 80)
    print("FIRST INITIALIZATION")
    print("=" * 80)

    engine1 = ocr_service.initialize()

    print(id(engine1))

    print()

    print("=" * 80)
    print("SECOND INITIALIZATION")
    print("=" * 80)

    engine2 = ocr_service.initialize()

    print(id(engine2))

    print()

    print("=" * 80)
    print("CHECKING SINGLETON")
    print("=" * 80)

    print(engine1 is engine2)


if __name__ == "__main__":
    main()