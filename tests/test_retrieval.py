import json
import sqlite3

import pytest

import app.retrieval as retrieval


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    """
    Build an isolated SQLite inventory for retrieval tests.
    """
    db_path = tmp_path / "test_inventory.db"

    conn = sqlite3.connect(db_path)

    conn.execute(
        """
        CREATE TABLE cars (
            listing_id INTEGER PRIMARY KEY,
            year INTEGER,
            make TEXT,
            model TEXT,
            trim TEXT,
            title TEXT,
            description TEXT,
            photo_url TEXT,
            price_aed REAL,
            mileage_km REAL,
            body_type TEXT,
            transmission TEXT,
            fuel_type TEXT
        )
        """
    )

    cars = [
        (
            1,
            2020,
            "toyota",
            "rav4",
            "gx",
            "Toyota RAV4 GX",
            "Panoramic roof, lane assist, GCC specification.",
            "https://example.com/rav4.jpg",
            55000,
            80000,
            "suv",
            "automatic",
            "petrol",
        ),
        (
            2,
            2019,
            "toyota",
            "camry",
            "se",
            "Toyota Camry SE",
            "Clean sedan with cruise control.",
            "https://example.com/camry.jpg",
            45000,
            95000,
            "sedan",
            "automatic",
            "petrol",
        ),
        (
            3,
            2021,
            "bmw",
            "x1",
            "xdrive20i",
            "BMW X1",
            "Panoramic roof and Apple CarPlay.",
            "https://example.com/x1.jpg",
            60000,
            70000,
            "suv",
            "automatic",
            "petrol",
        ),
        (
            4,
            2022,
            "nissan",
            "leaf",
            "sv",
            "Nissan Leaf",
            "Electric hatchback.",
            "https://example.com/leaf.jpg",
            58000,
            30000,
            "hatchback",
            "automatic",
            "electric",
        ),
        (
            5,
            2018,
            "toyota",
            "prado",
            "txl",
            "Toyota Prado",
            "Off-road SUV. Price not listed.",
            "https://example.com/prado.jpg",
            None,
            110000,
            "suv",
            "automatic",
            "petrol",
        ),
    ]

    conn.executemany(
        """
        INSERT INTO cars (
            listing_id,
            year,
            make,
            model,
            trim,
            title,
            description,
            photo_url,
            price_aed,
            mileage_km,
            body_type,
            transmission,
            fuel_type
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        cars,
    )

    conn.commit()
    conn.close()

    # Point retrieval.py at the temporary DB instead of data/inventory.db.
    monkeypatch.setattr(
        retrieval,
        "DB_PATH",
        str(db_path),
    )

    return db_path


def search(**kwargs):
    """Convenience wrapper that decodes search_inventory JSON."""
    return json.loads(
        retrieval.search_inventory(**kwargs)
    )


def test_search_by_make(test_database):
    result = search(make="Toyota")

    assert result["count"] == 3

    assert all(
        car["make"] == "toyota"
        for car in result["cars"]
    )


def test_price_range(test_database):
    result = search(
        make="Toyota",
        min_price=50000,
        max_price=60000,
    )

    assert result["count"] == 1

    car = result["cars"][0]

    assert car["listing_id"] == 1
    assert 50000 <= car["price_aed"] <= 60000


def test_missing_price_does_not_match_price_filter(test_database):
    result = search(
        make="Toyota",
        max_price=60000,
    )

    ids = {
        car["listing_id"]
        for car in result["cars"]
    }

    # Listing 5 has NULL price and must not satisfy a numeric constraint.
    assert 5 not in ids


def test_body_type_filter(test_database):
    result = search(
        body_type="suv",
    )

    assert result["count"] == 3

    assert all(
        car["body_type"] == "suv"
        for car in result["cars"]
    )


def test_combined_filters(test_database):
    result = search(
        make="Toyota",
        min_year=2020,
        max_price=60000,
        max_mileage=90000,
        body_type="suv",
        transmission="automatic",
    )

    assert result["count"] == 1
    assert result["cars"][0]["listing_id"] == 1


def test_fuel_type_filter(test_database):
    result = search(
        fuel_type="electric",
    )

    assert result["count"] == 1
    assert result["cars"][0]["model"] == "leaf"


def test_keyword_searches_description(test_database):
    result = search(
        keyword="panoramic roof",
    )

    ids = {
        car["listing_id"]
        for car in result["cars"]
    }

    assert 1 in ids
    assert 3 in ids


def test_search_results_do_not_include_description(test_database):
    result = search(
        make="BMW",
    )

    car = result["cars"][0]

    assert "description" not in car


def test_get_car_details_includes_description(test_database):
    result = json.loads(
        retrieval.get_car_details(3)
    )

    assert result["listing_id"] == 3
    assert "description" in result
    assert "Apple CarPlay" in result["description"]


def test_unknown_listing(test_database):
    result = json.loads(
        retrieval.get_car_details(9999)
    )

    assert "error" in result


def test_no_matches_returns_empty_result(test_database):
    result = search(
        make="Ferrari",
    )

    assert result["count"] == 0
    assert result["cars"] == []