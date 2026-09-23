import csv

import app.tools as tools


def test_sunday_viewing_is_rejected():
    # 2026-10-25 is a Sunday.
    result = tools.book_viewing(
        car_id="123",
        booking_datetime="2026-10-25T14:00:00",
        customer_name="Test User",
        phone="0500000000",
    )

    assert '"status": "error"' in result
    assert "Monday to Saturday" in result


def test_viewing_before_opening_is_rejected():
    result = tools.book_viewing(
        car_id="123",
        booking_datetime="2026-10-24T07:30:00",
        customer_name="Test User",
        phone="0500000000",
    )

    assert '"status": "error"' in result
    assert "8:00 AM" in result


def test_viewing_after_hours_is_rejected():
    result = tools.book_viewing(
        car_id="123",
        booking_datetime="2026-10-24T20:30:00",
        customer_name="Test User",
        phone="0500000000",
    )

    assert '"status": "error"' in result


def test_valid_viewing_is_accepted():
    result = tools.book_viewing(
        car_id="123",
        booking_datetime="2026-10-24T14:30:00",
        customer_name="Test User",
        phone="0500000000",
    )

    assert '"status": "success"' in result
    assert "2026-10-24 14:30" in result


def test_invalid_datetime_is_rejected():
    result = tools.book_viewing(
        car_id="123",
        booking_datetime="tomorrow afternoon",
        customer_name="Test User",
        phone="0500000000",
    )

    assert '"status": "error"' in result
    assert "Invalid date format" in result


def test_save_qualified_lead(tmp_path, monkeypatch):
    lead_file = tmp_path / "leads.csv"

    monkeypatch.setattr(
        tools,
        "LEADS_CSV",
        str(lead_file),
    )

    result = tools.save_qualified_lead(
        user_id="user_test",
        name="Test User",
        budget="60000 AED",
        preferred_car_type="SUV",
        contact_info="0500000000",
    )

    assert '"status": "success"' in result
    assert lead_file.exists()

    with open(
        lead_file,
        newline="",
        encoding="utf-8",
    ) as file:
        rows = list(
            csv.DictReader(file)
        )

    assert len(rows) == 1
    assert rows[0]["user_id"] == "user_test"
    assert rows[0]["name"] == "Test User"
    assert rows[0]["budget"] == "60000 AED"
    assert rows[0]["preferred_car_type"] == "SUV"