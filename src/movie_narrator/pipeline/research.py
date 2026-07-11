from ..models import Context


def research_plot(ctx: Context) -> Context:
    if not ctx.metadata.get("research_enabled"):
        ctx.status.research = "skipped"
        print("⏭ research_plot: research disabled")
        return ctx
    ctx.status.research = "skipped"
    print("⏭ research_plot: not implemented until M2")
    return ctx
