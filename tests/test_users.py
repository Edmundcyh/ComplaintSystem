def test_admin_can_list_and_filter_users(client, make_user):
    admin = make_user("admin")
    user = make_user()

    resp = client.get("/users/", headers=admin["headers"])
    assert resp.status_code == 200
    assert {u["email"] for u in resp.json()} == {admin["email"], user["email"]}
    assert all("password" not in u for u in resp.json())

    resp = client.get(
        "/users/", params={"email": user["email"]}, headers=admin["headers"]
    )
    assert [u["id"] for u in resp.json()] == [user["id"]]


def test_admin_can_change_roles(client, make_user):
    admin = make_user("admin")
    user = make_user()

    for action, role in [("make-approver", "approver"), ("make-admin", "admin")]:
        resp = client.put(f"/users/{user['id']}/{action}", headers=admin["headers"])
        assert resp.status_code == 204
        resp = client.get(
            "/users/", params={"email": user["email"]}, headers=admin["headers"]
        )
        assert resp.json()[0]["role"] == role


def test_non_admins_cannot_manage_users(client, make_user):
    target = make_user()
    for role in ("complainer", "approver"):
        user = make_user(role)
        assert client.get("/users/", headers=user["headers"]).status_code == 403
        resp = client.put(f"/users/{target['id']}/make-admin", headers=user["headers"])
        assert resp.status_code == 403
