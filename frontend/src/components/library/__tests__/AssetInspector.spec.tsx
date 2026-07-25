import { fireEvent, screen } from "@testing-library/react";
import { renderWithIntl } from "@/test/renderWithIntl";
import type { Character, Prop, Scene } from "@/store/projectStore";
import { describe, expect, it, vi } from "vitest";

import AssetInspector from "../AssetInspector";

const asset = {
  id: "character-1",
  name: "Tester",
  description: "A synchronized character",
} as Character;

describe("AssetInspector editing", () => {
  it.each([
    { locale: "en" as const, usageCount: 0, expected: "Never used" },
    { locale: "en" as const, usageCount: 1, expected: "Used once" },
    { locale: "en" as const, usageCount: 4, expected: "Used 4 times" },
    { locale: "zh" as const, usageCount: 0, expected: "从未使用" },
    { locale: "zh" as const, usageCount: 1, expected: "使用 1 次" },
    { locale: "zh" as const, usageCount: 4, expected: "使用 4 次" },
  ])(
    "renders the exact $locale usage label for count $usageCount",
    ({ locale, usageCount, expected }) => {
      renderWithIntl(
        <AssetInspector
          asset={asset}
          type="characters"
          sourceName="Global / Shared"
          sourceId="global"
          sourceKind="global"
          usageCount={usageCount}
          starred={false}
          onClose={vi.fn()}
          onToggleStar={vi.fn()}
          onEdit={vi.fn()}
          onDelete={vi.fn()}
          deleting={false}
        />,
        { locale },
      );

      expect(screen.getByText(expected)).toBeInTheDocument();
    },
  );

  it.each([
    { locale: "en" as const, label: "Edit asset" },
    { locale: "zh" as const, label: "编辑资产" },
  ])("shows a localized Edit action in $locale", ({ locale, label }) => {
    const onEdit = vi.fn();
    renderWithIntl(
      <AssetInspector
        asset={asset}
        type="characters"
        sourceName="Global / Shared"
        sourceId="global"
        sourceKind="global"
        usageCount={0}
        starred={false}
        onClose={vi.fn()}
        onToggleStar={vi.fn()}
        onEdit={onEdit}
        onDelete={vi.fn()}
        deleting={false}
      />,
      { locale },
    );

    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it.each([
    {
      locale: "en" as const,
      removedAction: "Generate more variants",
      removedBadge: "In-episode",
    },
    {
      locale: "zh" as const,
      removedAction: "生成更多变体",
      removedBadge: "剧集内生成",
    },
  ])("does not render the retired variant shortcut in $locale", ({ locale, removedAction, removedBadge }) => {
    renderWithIntl(
      <AssetInspector
        asset={asset}
        type="characters"
        sourceName="Series"
        sourceId="series-1"
        sourceKind="series"
        usageCount={0}
        starred={false}
        onClose={vi.fn()}
        onToggleStar={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        deleting={false}
      />,
      { locale },
    );

    expect(screen.queryByText(removedAction)).not.toBeInTheDocument();
    expect(screen.queryByText(removedBadge)).not.toBeInTheDocument();
  });

  it.each([
    { locale: "en" as const, label: "Delete asset", loading: "Deleting…" },
    { locale: "zh" as const, label: "删除资产", loading: "正在删除…" },
  ])("exposes a localized destructive action in $locale", ({ locale, label, loading }) => {
    const onDelete = vi.fn();
    const { rerender } = renderWithIntl(
      <AssetInspector
        asset={asset}
        type="characters"
        sourceName="Global / Shared"
        sourceId="global"
        sourceKind="global"
        usageCount={0}
        starred={false}
        onClose={vi.fn()}
        onToggleStar={vi.fn()}
        onEdit={vi.fn()}
        onDelete={onDelete}
        deleting={false}
      />,
      { locale },
    );

    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(onDelete).toHaveBeenCalledTimes(1);

    rerender(
      <AssetInspector
        asset={asset}
        type="characters"
        sourceName="Global / Shared"
        sourceId="global"
        sourceKind="global"
        usageCount={0}
        starred={false}
        onClose={vi.fn()}
        onToggleStar={vi.fn()}
        onEdit={vi.fn()}
        onDelete={onDelete}
        deleting
      />,
    );
    expect(screen.getByRole("button", { name: loading })).toBeDisabled();
  });

  it.each([
    {
      type: "characters" as const,
      asset: {
        ...asset,
        reference_sheet: {
          selected_image_id: "character-image",
          image_variants: [{
            id: "character-image",
            url: "assets/character.png",
            created_at: 1,
            prompt_used: "DISTINCT CHARACTER PROMPT",
          }],
        },
      } as Character,
    },
    {
      type: "scenes" as const,
      asset: {
        id: "scene-1",
        name: "Test scene",
        description: "Scene description",
        image_asset: {
          selected_id: "scene-image",
          variants: [{
            id: "scene-image",
            url: "assets/scene.png",
            created_at: 1,
            prompt_used: "DISTINCT SCENE PROMPT",
          }],
        },
      } as Scene,
    },
    {
      type: "props" as const,
      asset: {
        id: "prop-1",
        name: "Test prop",
        description: "Prop description",
        image_asset: {
          selected_id: "prop-image",
          variants: [{
            id: "prop-image",
            url: "assets/prop.png",
            created_at: 1,
            prompt_used: "DISTINCT PROP PROMPT",
          }],
        },
      } as Prop,
    },
  ])("does not expose generated prompts for $type in the compact inspector", ({ asset: promptAsset, type }) => {
    renderWithIntl(
      <AssetInspector
        asset={promptAsset}
        type={type}
        sourceName="Global / Shared"
        sourceId="global"
        sourceKind="global"
        usageCount={0}
        starred={false}
        onClose={vi.fn()}
        onToggleStar={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        deleting={false}
      />,
      { locale: "en" },
    );

    expect(screen.queryByText(/^Prompt$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/DISTINCT .* PROMPT/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit asset" })).toBeInTheDocument();
  });

  it("does not expose prompt content or a prompt heading in Chinese", () => {
    const promptAsset = {
      ...asset,
      reference_sheet: {
        selected_image_id: "character-image",
        image_variants: [{
          id: "character-image",
          url: "assets/character.png",
          created_at: 1,
          prompt_used: "不应出现在侧栏中的独特提示词",
        }],
      },
    } as Character;
    renderWithIntl(
      <AssetInspector
        asset={promptAsset}
        type="characters"
        sourceName="全局 / 共享"
        sourceId="global"
        sourceKind="global"
        usageCount={0}
        starred={false}
        onClose={vi.fn()}
        onToggleStar={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        deleting={false}
      />,
      { locale: "zh" },
    );

    expect(screen.queryByText("提示词")).not.toBeInTheDocument();
    expect(screen.queryByText("不应出现在侧栏中的独特提示词")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑资产" })).toBeInTheDocument();
  });
});
