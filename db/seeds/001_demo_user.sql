-- Optional demo rows for local smoke tests.
-- Safe to re-run: uses fixed UUIDs + ON CONFLICT DO NOTHING.

INSERT INTO users (id, email, display_name)
VALUES (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    'anustup@example.com',
    'Anustup Das'
)
ON CONFLICT (email) DO NOTHING;

INSERT INTO projects (id, user_id, name)
VALUES (
    'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    'Shell annual report 2024'
)
ON CONFLICT (id) DO NOTHING;
