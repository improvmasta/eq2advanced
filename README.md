# eq2advanced

Starter app for https://eq2advanced.jupiterns.org.

## Local

```bash
bash restart.sh
```

Expected local URL: http://localhost:8450

## Docker

```bash
docker compose up -d --build
```

The GitHub workflow builds and pushes:

```
ghcr.io/improvmasta/eq2advanced:main
```

The repository is private by default. The GHCR container package is intended to be public.
GitHub may require a one-time package settings change to make the first GHCR package public:
`https://github.com/users/improvmasta/packages/container/package/eq2advanced/settings`
