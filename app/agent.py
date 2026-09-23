import json
import logging
import os
import time

from litellm import completion

from app.memory import (
    get_user_profile,
    get_session_history,
    append_message,
    update_user_profile,
)
from app.retrieval import search_inventory, get_car_details
from app.tools import book_viewing, save_qualified_lead


logger = logging.getLogger(__name__)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_inventory",
            "description": (
                "Search the dubizzle used-car inventory using structured filters. "
                "Use this whenever the user asks about available cars or gives "
                "constraints such as make, model, year, budget, mileage, body type, "
                "transmission, fuel type, or a feature keyword."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "make": {
                        "type": "string",
                        "description": "Vehicle manufacturer, e.g. Toyota or BMW.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Vehicle model, e.g. Camry or X1.",
                    },
                    "min_year": {
                        "type": "integer",
                        "description": "Minimum manufacturing year.",
                    },
                    "max_year": {
                        "type": "integer",
                        "description": "Maximum manufacturing year.",
                    },
                    "min_price": {
                        "type": "number",
                        "description": "Minimum total vehicle price in AED.",
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Maximum total vehicle price in AED.",
                    },
                    "max_mileage": {
                        "type": "number",
                        "description": "Maximum mileage in kilometres.",
                    },
                    "body_type": {
                        "type": "string",
                        "enum": [
                            "suv",
                            "sedan",
                            "hatchback",
                            "coupe",
                            "convertible",
                            "pickup",
                            "van",
                            "wagon",
                            "other",
                        ],
                        "description": "Requested vehicle body type.",
                    },
                    "transmission": {
                        "type": "string",
                        "enum": [
                            "automatic",
                            "manual",
                        ],
                        "description": "Requested transmission.",
                    },
                    "fuel_type": {
                        "type": "string",
                        "enum": [
                            "petrol",
                            "diesel",
                            "hybrid",
                            "electric",
                        ],
                        "description": "Requested fuel type.",
                    },
                    "keyword": {
                        "type": "string",
                        "description": (
                            "A listing-specific feature or phrase to search for, "
                            "such as panoramic roof, Apple CarPlay, or lane assist."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_car_details",
            "description": (
                "Retrieve the full details and description for one specific "
                "inventory listing. Use this when the user asks for more information "
                "or specific features about a car already identified in search results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "listing_id": {
                        "type": "integer",
                        "description": "The listing ID of the car.",
                    },
                },
                "required": ["listing_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_viewing",
            "description": "Book a test drive or viewing for a specific car.",
            "parameters": {
                "type": "object",
                "properties": {
                    "car_id": {
                        "type": "string",
                        "description": "The listing ID of the car.",
                    },
                    "booking_datetime": {
                        "type": "string",
                        "description": (
                            "ISO 8601 datetime, for example "
                            "'2026-10-24T14:30:00'."
                        ),
                    },
                    "customer_name": {
                        "type": "string",
                    },
                    "phone": {
                        "type": "string",
                    },
                },
                "required": [
                    "car_id",
                    "booking_datetime",
                    "customer_name",
                    "phone",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_qualified_lead",
            "description": (
                "Save useful user preferences such as their budget or preferred "
                "car type to long-term memory. If name and contact information "
                "are also available, save the user as a qualified lead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                    },
                    "budget": {
                        "type": "string",
                    },
                    "preferred_car_type": {
                        "type": "string",
                    },
                    "contact_info": {
                        "type": "string",
                    },
                },
            },
        },
    },
]


def execute_tool(tool_call, user_id):
    """Execute a tool requested by the model."""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    logger.info("Executing tool: %s", name)
    started = time.perf_counter()

    try:
        if name == "search_inventory":
            return search_inventory(
                make=args.get("make"),
                model=args.get("model"),
                min_year=args.get("min_year"),
                max_year=args.get("max_year"),
                min_price=args.get("min_price"),
                max_price=args.get("max_price"),
                max_mileage=args.get("max_mileage"),
                body_type=args.get("body_type"),
                transmission=args.get("transmission"),
                fuel_type=args.get("fuel_type"),
                keyword=args.get("keyword"),
            )

        if name == "get_car_details":
            listing_id = args.get("listing_id")

            result = get_car_details(
                listing_id=listing_id
            )

            try:
                car = json.loads(result)

                if "error" not in car:
                    profile = get_user_profile(user_id)

                    viewed_cars = profile.get(
                        "recently_viewed_cars",
                        [],
                    )

                    viewed_car = {
                        "listing_id": car.get("listing_id"),
                        "year": car.get("year"),
                        "make": car.get("make"),
                        "model": car.get("model"),
                        "trim": car.get("trim"),
                        "title": car.get("title"),
                    }

                    # Remove an older occurrence of the same listing.
                    viewed_cars = [
                        existing_car
                        for existing_car in viewed_cars
                        if existing_car.get("listing_id")
                        != viewed_car["listing_id"]
                    ]

                    # Put the newest viewed car first.
                    viewed_cars.insert(
                        0,
                        viewed_car,
                    )

                    # Keep memory bounded.
                    viewed_cars = viewed_cars[:5]

                    update_user_profile(
                        user_id,
                        {
                            "recently_viewed_cars": viewed_cars
                        },
                    )

            except (json.JSONDecodeError, TypeError):
                pass

            return result

        if name == "book_viewing":
            return book_viewing(
                car_id=args.get("car_id"),
                booking_datetime=args.get("booking_datetime"),
                customer_name=args.get("customer_name"),
                phone=args.get("phone"),
            )

        if name == "save_qualified_lead":
            profile_updates = {}

            if args.get("budget"):
                profile_updates["budget"] = args["budget"]

            if args.get("preferred_car_type"):
                profile_updates["preferred_car_type"] = (
                    args["preferred_car_type"]
                )

            if profile_updates:
                update_user_profile(
                    user_id,
                    profile_updates,
                )

            name_value = args.get("name")
            contact_info = args.get("contact_info")

            # Store preferences in memory even when the user has not
            # provided enough information to become a qualified lead.
            if not name_value or not contact_info:
                return json.dumps({
                    "status": "success",
                    "message": (
                        "User preferences were saved to long-term memory. "
                        "A qualified lead was not created because name or "
                        "contact information was not provided."
                    ),
                })

            return save_qualified_lead(
                user_id=user_id,
                name=name_value,
                budget=args.get("budget", ""),
                preferred_car_type=args.get(
                    "preferred_car_type",
                    "",
                ),
                contact_info=contact_info,
            )

        return json.dumps({
            "error": f"Unknown tool: {name}"
        })

    finally:
        logger.info(
            "Tool %s completed in %.2fs",
            name,
            time.perf_counter() - started,
        )


def is_retryable_provider_error(error: Exception) -> bool:
    """Identify provider/network errors where trying a fallback is reasonable."""
    message = str(error).lower()

    retryable_markers = [
        "429",
        "503",
        "service unavailable",
        "serviceunavailable",
        "rate limit",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "high demand",
    ]

    return any(
        marker in message
        for marker in retryable_markers
    )


def call_llm(messages):
    """Call the configured LLM with provider-level retry support."""
    primary_model = os.getenv(
        "DUBIZZLE_LLM_MODEL",
        "gemini/gemini-3.8-flash",
    )

    fallback_model = os.getenv(
        "DUBIZZLE_LLM_FALLBACK",
        "gemini/gemini-3.5-flash-lite",
    )

    models = [primary_model]

    if fallback_model and fallback_model != primary_model:
        models.append(fallback_model)

    last_error = None

    for index, model in enumerate(models):
        started = time.perf_counter()

        try:
            logger.info("Calling LLM model: %s", model)

            response = completion(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                num_retries=3,
            )

            logger.info(
                "LLM call completed in %.2fs using %s",
                time.perf_counter() - started,
                model,
            )

            return response

        except Exception as e:
            last_error = e

            logger.warning(
                "LLM call failed after %.2fs using %s: %s",
                time.perf_counter() - started,
                model,
                e,
            )

            # Coding/schema errors should fail immediately rather than
            # being hidden by an unrelated model fallback.
            if not is_retryable_provider_error(e):
                raise

            if index == len(models) - 1:
                break

            logger.info("Trying configured fallback model.")

    raise last_error


def chat_with_agent(
    session_id: str,
    user_id: str,
    user_message: str,
) -> str:
    profile = get_user_profile(user_id)

    if profile:
        profile_context = (
            "\n[RETURNING USER PROFILE MEMORY]: "
            f"{json.dumps(profile)}"
        )
    else:
        profile_context = (
            "\n[RETURNING USER PROFILE MEMORY]: None"
        )

    system_prompt = f"""
You are a helpful automotive assistant for dubizzle cars.

Your job is to help users:
- search the available used-car inventory
- answer questions about specific listings
- arrange viewing requests
- remember useful buyer preferences
- qualify leads when contact information is provided

{profile_context}

Inventory rules:
- Use search_inventory whenever a user asks about available cars.
- Convert explicit constraints such as make, model, year, budget, mileage,
  body type, transmission, and fuel type into structured search filters.
- Use keyword for listing-specific features such as panoramic roof,
  Apple CarPlay, lane assist, or similar features.
- Values such as price, mileage, body type, transmission, and fuel type
  may be missing from a listing. Treat missing values as "not listed".
  Never invent a missing value.
- Do not claim that a car satisfies a numeric constraint unless the
  structured search result confirms it.
- Search results intentionally contain compact information.
- Use get_car_details only when the user asks for deeper information about
  a particular listing. Do not retrieve full details for every search result.

Viewing rules:
- Before booking a viewing, collect the listing ID, customer name,
  phone number, and proposed time.
- Viewings are available Monday through Saturday from 8:00 AM to 8:00 PM.
  The booking tool enforces this rule.

Capability rules:
- Only claim actions that are supported by the available tools.
- You cannot contact dealers, call sellers, send messages, browse external dealer systems,
  verify information outside the supplied inventory, or perform follow-up actions later.
- If listing information is missing, say that it is not listed in the available data.
- Do not offer to "check with the dealer", "contact the seller", "confirm with the dealership",
  or similar actions.
- You may instead offer to show other matching listings or validate a viewing request.

Memory rules:
- The returning-user profile may contain previously saved budget,
  vehicle preferences, and recently viewed cars.
- Use those stored values when relevant.
- recently_viewed_cars is ordered from most recently viewed to least recent.
- If the user asks "what car was I looking at?" or similar, refer to the
  most recent entry in recently_viewed_cars.
- If the user asks "what cars was I looking at?", "which cars did I view?",
  or similar, summarize the stored recently_viewed_cars.
- If the user refers to "the first one", "the BMW I looked at", or another
  previously viewed vehicle, use recently_viewed_cars to resolve the reference
  when the match is clear.
- Do not invent vehicle history or preferences that are not stored.

Response rules:
- Stay focused on automotive queries, car buying, and viewing requests.
- Politely decline any engagement in conversation about competitors of dubizzle.
- Politely decline any engagement in conversation about topics unrelated to cars.
- Format inventory results clearly and compactly in Markdown.
- For each vehicle, use this structure:

  ### [number]. [year] [make] [model]
  **Listing #ID** · [price if available] · [mileage if available]
  - Trim: [trim if useful]
  - Body: [body type if available]
  - Transmission: [transmission if available]
  - Fuel: [fuel type if available]
  <img src="[PHOTO_URL]">

- Do not display a combined line such as
  "Price / Mileage / Body Type / Transmission / Fuel Type: Not listed".
- Omit unavailable attributes instead of listing every missing field.
- Never invent missing listing information.
- Keep each vehicle summary concise.
- If the user's saved profile affects the recommendation, place that information
  after the vehicle results as a short Markdown blockquote beginning with
  "> Profile note:".
- Do not repeatedly ask whether the user wants to book after every individual car.
  Ask once at the end of the full result set.s
"""

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]

    history = get_session_history(session_id)
    messages.extend(history)

    messages.append({
        "role": "user",
        "content": user_message,
    })

    append_message(
        session_id,
        "user",
        user_message,
    )

    try:
        response = call_llm(messages)
        message = response.choices[0].message

        loop_count = 0
        max_tool_rounds = 3

        while (
            getattr(message, "tool_calls", None)
            and loop_count < max_tool_rounds
        ):
            messages.append(
                message.model_dump()
            )

            for tool_call in message.tool_calls:
                tool_result = execute_tool(
                    tool_call,
                    user_id,
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.function.name,
                    "content": str(tool_result),
                })

            response = call_llm(messages)
            message = response.choices[0].message
            loop_count += 1

        final_text = message.content

        if final_text is None:
            final_text = (
                "I processed your request, but couldn't generate "
                "a final response."
            )

    except Exception as e:
        logger.exception(
            "Agent request failed for user=%s, session=%s",
            user_id,
            session_id,
        )

        if is_retryable_provider_error(e):
            final_text = (
                "*The AI service is temporarily unavailable. "
                "Please try again in a moment.*"
            )
        else:
            final_text = (
                "*An internal AI error occurred. "
                "Please try again in a moment.*"
            )

    # Keep both sides of the conversation in short-term session memory.
    append_message(
        session_id,
        "assistant",
        final_text,
    )

    return final_text
