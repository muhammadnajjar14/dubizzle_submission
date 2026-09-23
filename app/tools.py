import pandas as pd
import os
from datetime import datetime

LEADS_CSV = "data/leads.csv"

def book_viewing(car_id: str, booking_datetime: str, customer_name: str, phone: str) -> str:
    """Validate a requested viewing slot.

    This prototype does not persist bookings; it only validates
    whether the requested slot satisfies the business rules.
    """
    try:
        dt = datetime.fromisoformat(booking_datetime)
        
        # Viewings are only available Monday-Saturday, 08:00-20:00.
        if dt.weekday() == 6: 
            return '{"status": "error", "message": "Viewings are only available Monday to Saturday."}'
        if not (8 <= dt.hour < 20):
            return '{"status": "error", "message": "Slots are only available between 8:00 AM and 8:00 PM."}'
        
        # Booking persistence is outside the scope of this prototype.
        return f'{{"status": "success", "message": "Viewing request validated for {customer_name} on {dt.strftime("%Y-%m-%d %H:%M")}"}}'
    except ValueError:
        return '{"status": "error", "message": "Invalid date format. Use ISO 8601 (YYYY-MM-DDTHH:MM:SS)."}'


def save_qualified_lead(user_id: str, name: str, budget: str, preferred_car_type: str, contact_info: str) -> str:
    """Saves qualified lead preferences to a local CSV file."""
    lead_data = {
        "user_id": user_id,
        "name": name,
        "budget": budget,
        "preferred_car_type": preferred_car_type,
        "contact_info": contact_info,
        "timestamp": datetime.now().isoformat()
    }
    
    df = pd.DataFrame([lead_data])
    
    if not os.path.exists(LEADS_CSV):
        df.to_csv(LEADS_CSV, index=False)
    else:
        df.to_csv(LEADS_CSV, mode='a', header=False, index=False)
        
    return '{"status": "success", "message": "Lead qualified and saved successfully."}'
