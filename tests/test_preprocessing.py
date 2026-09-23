from scripts.preprocess import (
    extract_price,
    extract_mileage,
    extract_transmission,
)


def test_cash_price_beats_monthly_payment():
    text = """
    Payment plans:
    AED 2,111 monthly for 5 years
    AED 1,876 monthly for 5 years
    AED 119,750 in cash
    Mileage: 68,000 km
    """

    assert extract_price(text) == 119750


def test_salary_requirement_is_not_vehicle_price():
    text = """
    PRICE REDUCED 35000 AED

    Mazda 3 2019 Model GCC Spec.

    Salary Required:- AED 3000
    Bank Processing Fee: AED 1000
    Registration Fee: AED 1200
    """

    assert extract_price(text) == 35000

def test_monthly_price_in_title_is_not_total_price():
    text = """
    GLS 63 AMG | From AED 5,805/mo | Up to 3Y Warranty
    Mercedes-Benz GLS 63 AMG Year: 2021
    Mileage: 23 900 kms
    """

    assert extract_price(text) is None
    assert extract_mileage(text) == 23900
    
def test_monthly_payment_is_not_vehicle_price():
    text = """
    1,349,999 AED / 25,683 AED per Month
    with 20% Down Payment over 5 Years.
    """

    assert extract_price(text) == 1349999


def test_top_speed_is_not_mileage():
    text = """
    Horse power - 542 Hp
    Max Speed - 198 mph / 318 km/h
    Acceleration - 0-100 km/h in 4.0 seconds
    """

    assert extract_mileage(text) is None


def test_explicit_mileage_is_extracted():
    text = """
    Model - 2019
    Mileage - 56,000 KM
    Japanese Specs
    """

    assert extract_mileage(text) == 56000


def test_done_km_is_treated_as_mileage():
    text = """
    2017 Rolls Royce Dawn done 42,000KM GCC.
    """

    assert extract_mileage(text) == 42000


def test_automatic_mirror_does_not_mean_automatic_transmission():
    text = """
    Fully Automatic Power Mirror
    Power Windows
    Power Steering
    """

    assert extract_transmission(text) is None


def test_explicit_automatic_transmission():
    text = """
    Transmission: 8-speed automatic (AT)
    """

    assert extract_transmission(text) == "automatic"