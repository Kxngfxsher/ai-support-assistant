def ask(client, text, conversation_id=None):
    response = client.post("/api/chat", json={"text": text, "conversation_id": conversation_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_demo_history_is_seeded(client):
    config = client.get("/api/config").json()
    assert config["engine"] == "rules" and config["sections"] == 35 and len(config["quick_questions"]) == 4

    stats = client.get("/api/stats").json()
    assert stats["conversations"] == 14 and stats["questions"] == 18
    assert stats["escalated"] == 5 and stats["resolved_by_bot"] == 13
    assert stats["deflection_rate"] == 0.72
    assert stats["open_tickets"] == 3 and stats["urgent_tickets"] == 1
    assert {topic["topic"] for topic in stats["topics"]} >= {"Доставка", "Возврат и обмен", "Статус заказа"}

    tickets = client.get("/api/tickets").json()
    assert [ticket["status"] for ticket in tickets] == ["open"] * 3 + ["closed"] * 2
    assert tickets[0]["priority"] == "high" and tickets[0]["reason_label"] == "Жалоба или конфликт"

    gaps = client.get("/api/gaps").json()
    assert gaps[0] == {**gaps[0], "question": "Есть ли у вас подарочные сертификаты?", "count": 2}
    assert len(gaps) == 3
    assert len(client.get("/api/kb").json()) == 35
    assert len(client.get("/api/conversations").json()) == 14


def test_answer_with_source_and_follow_up(client):
    first = ask(client, "Сколько стоит доставка?")
    message = first["message"]
    assert message["action"] == "answer" and "390 ₽" in message["text"]
    assert message["sources"] == [{"id": "dostavka/stoimost", "title": "Стоимость доставки", "doc_title": "Доставка"}]
    assert first["ticket"] is None

    second = ask(client, "А рассрочка есть?", first["conversation_id"])
    assert second["conversation_id"] == first["conversation_id"]
    assert second["message"]["sources"][0]["id"] == "oplata/rassrochka"

    conversation = client.get(f"/api/conversations/{first['conversation_id']}").json()
    assert [row["role"] for row in conversation["messages"]] == ["user", "assistant", "user", "assistant"]
    assert conversation["open_ticket"] is None


def test_handoff_to_operator_and_back(client):
    escalated = ask(client, "Можно ли оплатить заказ криптовалютой через знакомого?")
    ticket = escalated["ticket"]
    assert escalated["message"]["action"] == "escalate" and ticket["status"] == "open"
    assert ticket["reason"] == "no_answer" and ticket["channel"] == "web"

    again = ask(client, "Позовите оператора", escalated["conversation_id"])
    assert again["ticket"]["id"] == ticket["id"]  # второе обращение в том же диалоге не создаётся

    reply = client.post(f"/api/tickets/{ticket['id']}/reply", json={"text": "Здравствуйте! Криптовалюту не принимаем."})
    assert reply.status_code == 200 and reply.json()["status"] == "closed"
    conversation = client.get(f"/api/conversations/{escalated['conversation_id']}").json()
    assert conversation["messages"][-1]["role"] == "operator"
    assert conversation["open_ticket"] is None
    assert client.post(f"/api/tickets/{ticket['id']}/reply", json={"text": "ещё раз"}).status_code == 409


def test_order_dialog_over_api(client):
    first = ask(client, "Где мой заказ?")
    assert first["message"]["action"] == "clarify"
    second = ask(client, "10481", first["conversation_id"])
    assert second["message"]["action"] == "answer" and "готов к выдаче" in second["message"]["text"]
    assert second["message"]["meta"]["order"] == "10481"


def test_complaint_gets_urgent_ticket(client):
    result = ask(client, "Хочу пожаловаться: курьер нагрубил и уехал")
    assert result["ticket"]["priority"] == "high" and result["ticket"]["reason"] == "complaint"
    assert result["message"]["text"].startswith("Мне жаль")


def test_validation_and_errors(client):
    assert client.post("/api/chat", json={"text": ""}).status_code == 422
    assert client.post("/api/chat", json={"text": "   "}).status_code == 422
    assert client.post("/api/chat", json={"text": "x" * 2001}).status_code == 422
    assert client.post("/api/chat", json={"text": "Привет", "conversation_id": 9999}).status_code == 404
    assert client.get("/api/conversations/9999").status_code == 404
    assert client.post("/api/tickets/9999/reply", json={"text": "ответ"}).status_code == 404
    assert client.get("/").status_code == 200


def test_reset_restores_demo(client):
    ask(client, "Сколько стоит доставка?")
    assert client.get("/api/stats").json()["conversations"] == 15
    assert client.post("/api/demo/reset").json() == {"seeded": 14}
    assert client.get("/api/stats").json()["conversations"] == 14
