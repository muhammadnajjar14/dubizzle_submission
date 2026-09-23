import json
import os
import sqlite3

import pandas as pd


DB_PATH = "data/inventory.db"
PROCESSED_PATH = "data/cars.csv"


def initialize_database():
    """Build the SQLite inventory database from the processed inventory CSV."""
    if not os.path.exists(PROCESSED_PATH):
        print(
            f"Error: {PROCESSED_PATH} not found. "
            "Run scripts/preprocess.py first."
        )
        return False

    try:
        df = pd.read_csv(PROCESSED_PATH)

        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

        conn = sqlite3.connect(DB_PATH)

        try:
            df.to_sql(
                "cars",
                conn,
                if_exists="replace",
                index=False,
            )
        finally:
            conn.close()

        print(
            f"Database initialized successfully with "
            f"{len(df)} records."
        )
        print(
            f"Available columns in DB: "
            f"{list(df.columns)}"
        )

        return True

    except Exception as e:
        print(f"Failed to initialize database: {e}")
        return False


def search_inventory(
    make: str = None,
    model: str = None,
    min_year: int = None,
    max_year: int = None,
    min_price: float = None,
    max_price: float = None,
    max_mileage: float = None,
    body_type: str = None,
    transmission: str = None,
    fuel_type: str = None,
    keyword: str = None,
) -> str:
    """
    Search inventory using structured filters.

    Returns compact listing summaries so full descriptions are not
    unnecessarily passed back into the LLM context.
    """
    if not os.path.exists(DB_PATH):
        return json.dumps(
            {"error": "Database not initialized."}
        )

    query = """
        SELECT
            listing_id,
            year,
            make,
            model,
            trim,
            title,
            price_aed,
            mileage_km,
            body_type,
            transmission,
            fuel_type,
            photo_url
        FROM cars
        WHERE 1=1
    """

    params = []

    if make:
        query += " AND LOWER(make) LIKE ?"
        params.append(f"%{make.lower()}%")

    if model:
        query += " AND LOWER(model) LIKE ?"
        params.append(f"%{model.lower()}%")

    if min_year is not None:
        query += " AND year >= ?"
        params.append(min_year)

    if max_year is not None:
        query += " AND year <= ?"
        params.append(max_year)

    if min_price is not None:
        query += " AND price_aed >= ?"
        params.append(min_price)

    if max_price is not None:
        query += " AND price_aed <= ?"
        params.append(max_price)

    if max_mileage is not None:
        query += " AND mileage_km <= ?"
        params.append(max_mileage)

    if body_type:
        query += " AND LOWER(body_type) = ?"
        params.append(body_type.lower())

    if transmission:
        query += " AND LOWER(transmission) = ?"
        params.append(transmission.lower())

    if fuel_type:
        query += " AND LOWER(fuel_type) = ?"
        params.append(fuel_type.lower())

    if keyword:
        query += """
            AND (
                LOWER(COALESCE(title, '')) LIKE ?
                OR LOWER(COALESCE(description, '')) LIKE ?
                OR LOWER(COALESCE(trim, '')) LIKE ?
            )
        """

        keyword_value = f"%{keyword.lower()}%"

        params.extend([
            keyword_value,
            keyword_value,
            keyword_value,
        ])

    # Use a sensible ordering depending on the user's constraints.
    if min_price is not None or max_price is not None:
        query += """
            ORDER BY
                CASE WHEN price_aed IS NULL THEN 1 ELSE 0 END,
                price_aed ASC,
                year DESC
        """
    elif max_mileage is not None:
        query += """
            ORDER BY
                CASE WHEN mileage_km IS NULL THEN 1 ELSE 0 END,
                mileage_km ASC,
                year DESC
        """
    else:
        query += " ORDER BY year DESC, listing_id ASC"

    query += " LIMIT 5"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            query,
            params,
        ).fetchall()

        results = [
            dict(row)
            for row in rows
        ]

        if not results:
            return json.dumps({
                "count": 0,
                "cars": [],
                "message": (
                    "No cars found matching those criteria."
                ),
            })

        return json.dumps({
            "count": len(results),
            "cars": results,
        })

    except Exception as e:
        return json.dumps({
            "error": f"SQL Error: {str(e)}"
        })

    finally:
        conn.close()


def get_car_details(listing_id: int) -> str:
    """
    Return detailed information for one listing.

    Full descriptions are retrieved only when the conversation
    actually needs listing-specific details.
    """
    if not os.path.exists(DB_PATH):
        return json.dumps(
            {"error": "Database not initialized."}
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        row = conn.execute(
            """
            SELECT *
            FROM cars
            WHERE listing_id = ?
            """,
            (listing_id,),
        ).fetchone()

        if row is None:
            return json.dumps({
                "error": (
                    f"No car found with listing ID "
                    f"{listing_id}."
                )
            })

        result = dict(row)

        # Avoid sending an extremely large listing into model context.
        description = result.get("description")

        if description:
            result["description"] = description[:5000]

        return json.dumps(result)

    except Exception as e:
        return json.dumps({
            "error": f"SQL Error: {str(e)}"
        })

    finally:
        conn.close()


if __name__ == "__main__":
    initialize_database()