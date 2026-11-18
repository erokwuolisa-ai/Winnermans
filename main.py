import os
import stripe
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="ProPredict Sports Engine", version="1.0.0")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_mockkey")
ENDPOINT_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_mockkey")
DOMAIN_URL = os.getenv("DOMAIN_URL", "http://localhost:8000")

def get_db_connection():
    return psycopg2.connect(
        os.getenv("DATABASE_URL", "postgres://user:password@localhost:5432/sports_db"),
        cursor_factory=RealDictCursor
    )

class MatchEvaluationRequest(BaseModel):
    team_a_last_5_matches: list[dict] # Elements: {"home_score": int, "away_score": int}
    team_b_last_5_matches: list[dict] # Elements: {"home_score": int, "away_score": int}
    chosen_team_last_5: list[dict]   # Elements: {"goals_scored": int, "goals_conceded": int}
    opposition_last_5: list[dict]     # Elements: {"goals_scored": int}

@app.post("/api/evaluate-prediction")
def evaluate_prediction(data: MatchEvaluationRequest):
    """
    Evaluates matches based on user's exact criteria:
    1. Over 1.5 Goals: Both teams must have total goals >= 2 in all of their previous 5 matches.
    2. 3-Way Handicap: Chosen team must have scored >= 1 or kept clean sheet in previous 5 matches,
       and opposition must NOT have scored > 1 goal in their previous 5 matches.
    """
    
    # 1. Over 1.5 Goals Logic
    team_a_over_1_5 = all((m["home_score"] + m["away_score"]) >= 2 for m in data.team_a_last_5_matches)
    team_b_over_1_5 = all((m["home_score"] + m["away_score"]) >= 2 for m in data.team_b_last_5_matches)
    over_1_5_valid = team_a_over_1_5 and team_b_over_1_5

    # 2. 3-Way Handicap Logic
    chosen_team_valid = all(
        m["goals_scored"] >= 1 or m["goals_conceded"] == 0 
        for m in data.chosen_team_last_5
    )
    opposition_valid = all(
        m["goals_scored"] <= 1 
        for m in data.opposition_last_5
    )
    handicap_3_way_valid = chosen_team_valid and opposition_valid

    return {
        "status": "success",
        "filters_passed": {
            "over_1_5_goals": over_1_5_valid,
            "three_way_handicap": handicap_3_way_valid
        }
    }

class CheckoutRequest(BaseModel):
    user_email: str
    price_id: str

@app.post("/create-checkout-session")
def create_checkout_session(req: CheckoutRequest):
    try:
        session = stripe.checkout.Session.create(
            customer_email=req.user_email,
            payment_method_types=["card"],
            line_items=[{"price": req.price_id, "quantity": 1}],
            mode="subscription",
            success_url=f"{DOMAIN_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN_URL}/cancel"
        )
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, ENDPOINT_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook signature verification failed")
    
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_email")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET subscription_status = 'active' WHERE email = %s", (customer_email,))
        conn.commit()
        cur.close()
        conn.close()
        
    return {"status": "received"}
