"""The tools about the signed-in account."""

from datetime import UTC, datetime

from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from candlestack.accounts import Scope, get_accounts_service
from candlestack.mcp.convention import Example, ToolSpec, reads, tool_call


class MeResult(BaseModel):
    """The user the agent acts for, and what its API key may do."""

    id: str = Field(
        pattern=r"^usr_[a-z0-9]{10}$",
        description="Public user ID, shown in Settings and given to support",
    )
    display_name: str = Field(description="Name shown in the app")
    email: str = Field(description="From the identity provider the user signs in with")
    created_at: datetime = Field(description="When the account was created (UTC)")
    scopes: list[Scope] = Field(
        description="What the API key may do: read (every tool that only reads), backtests "
        "(start, rename, star and cancel backtests), models (upload, manifests, rename, star "
        "and delete models), publish (publish and unpublish backtests)"
    )


async def get_me(ctx: Context) -> MeResult:
    """Who you act for: the CandleStack user who signed you in (an API key or OAuth), and the
    scopes of that sign-in, which decide the tools you may call. Call it first to check the
    connection, and when a tool answers the problem missing-scope."""
    call = tool_call(ctx)
    async with call.db() as db:
        state = call.state
        service = get_accounts_service(
            call.settings, state.redis, db, state.http, state.account_deletion_hooks
        )
        me = await service.me(call.user)
    return MeResult(
        id=me.id,
        display_name=me.display_name,
        email=me.email,
        created_at=me.created_at,
        scopes=call.scopes,
    )


TOOLS = (
    ToolSpec(
        get_me,
        name="get_me",
        title="Who am I",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={},
            result=MeResult(
                id="usr_7f3k2m9qx4",
                display_name="Arsen Labovich",
                email="arsen@example.com",
                created_at=datetime(2026, 10, 1, 9, 30, tzinfo=UTC),
                scopes=[Scope.read, Scope.backtests],
            ),
        ),
    ),
)
