import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";
import EpisodeMiniList from "../EpisodeMiniList";

const { getSeriesEpisodes } = vi.hoisted(() => ({
    getSeriesEpisodes: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
    api: { getSeriesEpisodes },
}));

describe("EpisodeMiniList", () => {
    beforeEach(() => {
        window.location.hash = "#/series/series-1/episode/episode-1";
        getSeriesEpisodes.mockReset();
        getSeriesEpisodes.mockResolvedValue([
            { id: "episode-1", title: "当前集", episode_number: 1 },
            { id: "episode-2", title: "下一集", episode_number: 2 },
        ]);
    });

    it("keeps the currently viewed episode title highlighted when the episode changes", async () => {
        const view = renderWithIntl(
            <EpisodeMiniList
                seriesId="series-1"
                currentProjectId="episode-1"
            />,
        );

        const firstEpisode = await screen.findByRole("button", { name: /当前集/ });
        const secondEpisode = screen.getByRole("button", { name: /下一集/ });
        expect(screen.queryByText(/本系列/)).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /添加新集/ })).not.toBeInTheDocument();
        expect(firstEpisode).toHaveAttribute("aria-current", "page");
        expect(screen.getByText("当前集")).toHaveClass("font-semibold", "text-primary");
        expect(secondEpisode).not.toHaveAttribute("aria-current");
        expect(screen.getByText("下一集")).not.toHaveClass("font-semibold", "text-primary");

        fireEvent.click(secondEpisode);
        expect(window.location.hash).toBe("#/series/series-1/episode/episode-2");

        view.rerender(
            <EpisodeMiniList
                seriesId="series-1"
                currentProjectId="episode-2"
            />,
        );

        expect(firstEpisode).not.toHaveAttribute("aria-current");
        expect(screen.getByText("当前集")).not.toHaveClass("font-semibold", "text-primary");
        expect(secondEpisode).toHaveAttribute("aria-current", "page");
        expect(screen.getByText("下一集")).toHaveClass("font-semibold", "text-primary");
    });
});
