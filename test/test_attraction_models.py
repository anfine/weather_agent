import os
import unittest


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import Base
from models.attraction import Attraction, AttractionAlias


class AttractionModelTests(unittest.TestCase):
    def test_alias_must_be_unique(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)

        try:
            with Session(engine) as session:
                session.add_all(
                    [
                        Attraction(
                            id="first-attraction",
                            name="第一个景点",
                            coverage="single_point",
                            weather_notice=None,
                            aliases=[AttractionAlias(alias="共同别名")],
                        ),
                        Attraction(
                            id="second-attraction",
                            name="第二个景点",
                            coverage="single_point",
                            weather_notice=None,
                            aliases=[AttractionAlias(alias="共同别名")],
                        ),
                    ]
                )

                with self.assertRaises(IntegrityError):
                    session.flush()
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
