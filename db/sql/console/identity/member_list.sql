SELECT m.user_id,m.client_id,u.email,u.display_name,m.role,m.is_enabled
FROM console_memberships m JOIN console_users u ON u.id=m.user_id WHERE m.client_id=%s ORDER BY u.email;
