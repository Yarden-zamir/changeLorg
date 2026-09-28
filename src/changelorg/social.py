from string import ascii_letters, digits

import httpx

X_HOSTS = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "mobile.x.com",
}


def x_username(url: httpx.URL) -> str:
    parts = url.path.strip("/").split("/")
    username = parts[0]
    reserved = {
        "home",
        "explore",
        "search",
        "notifications",
        "messages",
        "settings",
        "i",
        "intent",
        "share",
        "login",
        "logout",
        "signup",
        "hashtag",
        "tos",
        "privacy",
    }
    if (
        url.host not in X_HOSTS
        or not 1 <= len(username) <= 15
        or any(char not in ascii_letters + digits + "_" for char in username)
        or username.lower() in reserved
    ):
        raise ValueError("Enter an X profile or post URL")
    if len(parts) != 1 and not (
        len(parts) == 3
        and parts[1] == "status"
        and parts[2].isascii()
        and parts[2].isdigit()
    ):
        raise ValueError(
            "Use an X profile or post URL, not lists, searches, or private pages"
        )
    return username


def social_url(url: httpx.URL) -> httpx.URL:
    if url.host in X_HOSTS:
        return httpx.URL(f"https://x.com/{x_username(url)}")
    if url.host in {"bsky.app", "www.bsky.app"}:
        parts = url.path.strip("/").split("/")
        if (
            len(parts) < 2
            or parts[0] != "profile"
            or not parts[1]
            or any(char not in ascii_letters + digits + ".:-_" for char in parts[1])
        ):
            raise ValueError("Enter a Bluesky profile or post URL")
        if (
            len(parts) != 2
            and parts[2:] != ["rss"]
            and not (len(parts) == 4 and parts[2] == "post")
        ):
            raise ValueError("Enter a Bluesky profile or post URL")
        return httpx.URL(f"https://bsky.app/profile/{parts[1]}/rss")
    return url
