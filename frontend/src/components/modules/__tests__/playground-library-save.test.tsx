import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({
  playgroundApi: {
    saveToLibrary: vi.fn(),
  },
}));

import { playgroundApi } from '@/lib/api';
import DetailPanel from '@/components/modules/playground/DetailPanel';
import ResultCard from '@/components/modules/playground/ResultCard';
import {
  usePlaygroundStore,
  type PlaygroundGeneration,
} from '@/components/modules/playground/usePlaygroundStore';
import { renderWithIntl } from '@/test/renderWithIntl';
import { useToastStore } from '@/store/toastStore';

const generation: PlaygroundGeneration = {
  id: 'generation-1',
  mode: 't2i',
  model_id: 'gpt-image-2',
  prompt: 'A carved brass compass',
  input_media: [],
  parameters: { size: '1536x1024', quality: 'auto' },
  batch_size: 1,
  outputs: [{
    id: 'output-1',
    media_path: 'playground/images/compass.png',
    media_type: 'image',
    saved_to_library: false,
  }],
  status: 'completed',
  created_at: '2026-07-29T02:00:00Z',
};

const videoGeneration: PlaygroundGeneration = {
  ...generation,
  id: 'video-generation',
  mode: 't2v',
  outputs: [{
    id: 'video-output',
    media_path: 'playground/videos/harbor.mp4',
    media_type: 'video',
    saved_to_library: false,
  }],
};

beforeEach(() => {
  vi.mocked(playgroundApi.saveToLibrary).mockReset();
  vi.mocked(playgroundApi.saveToLibrary).mockImplementation(
    async (_generationId, _outputId, category) => ({ ok: true, category }),
  );
  usePlaygroundStore.setState({
    history: [generation],
    activeGenerationIds: [],
  });
  useToastStore.getState().clear();
});

afterEach(() => {
  useToastStore.getState().clear();
  vi.restoreAllMocks();
});

describe('Playground library classification', () => {
  it('uses the native category combobox and requires an explicit selection', async () => {
    renderWithIntl(<ResultCard generation={generation} />, { locale: 'en' });

    const category = screen.getByRole('combobox', { name: 'Save to library' });
    expect(within(category).getByRole('option', { name: 'Characters' })).toBeInTheDocument();
    expect(within(category).getByRole('option', { name: 'Scenes' })).toBeInTheDocument();
    expect(within(category).getByRole('option', { name: 'Props' })).toBeInTheDocument();
    expect(playgroundApi.saveToLibrary).not.toHaveBeenCalled();

    fireEvent.change(category, { target: { value: 'prop' } });

    await waitFor(() => {
      expect(playgroundApi.saveToLibrary).toHaveBeenCalledWith(
        'generation-1',
        'output-1',
        'prop',
      );
    });
    expect(usePlaygroundStore.getState().history[0].outputs[0]).toMatchObject({
      saved_to_library: true,
      library_category: 'prop',
    });
  });

  it('shows a localized ResultCard toast and leaves the output unsaved on failure', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(playgroundApi.saveToLibrary).mockRejectedValueOnce(new Error('offline'));
    renderWithIntl(<ResultCard generation={generation} />, { locale: 'en' });

    const category = screen.getByRole('combobox', { name: 'Save to library' });
    fireEvent.change(category, { target: { value: 'character' } });

    await waitFor(() => {
      expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
        kind: 'error',
        title: 'Could not save to the library. Please try again.',
      });
    });
    expect(usePlaygroundStore.getState().history[0].outputs[0].saved_to_library).toBe(false);
    await waitFor(() => expect(category).toBeEnabled());
  });

  it('uses the category persisted by an idempotent replay response', async () => {
    vi.mocked(playgroundApi.saveToLibrary).mockResolvedValueOnce({
      ok: true,
      category: 'scene',
    });
    renderWithIntl(<ResultCard generation={generation} />, { locale: 'en' });

    fireEvent.change(screen.getByRole('combobox', { name: 'Save to library' }), {
      target: { value: 'prop' },
    });

    await waitFor(() => {
      expect(usePlaygroundStore.getState().history[0].outputs[0]).toMatchObject({
        saved_to_library: true,
        library_category: 'scene',
      });
    });
  });

  it('shows a localized DetailPanel toast and leaves the output unsaved on failure', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(playgroundApi.saveToLibrary).mockRejectedValueOnce(new Error('offline'));
    renderWithIntl(
      <DetailPanel
        generation={generation}
        allGenerations={[generation]}
        focusOutputId="output-1"
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
      { locale: 'zh' },
    );

    const category = screen.getByRole('combobox', { name: '保存到资产库' });
    expect(within(category).getByRole('option', { name: '场景' })).toBeInTheDocument();
    fireEvent.change(category, { target: { value: 'scene' } });

    await waitFor(() => {
      expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
        kind: 'error',
        title: '无法保存到资产库，请稍后重试。',
      });
    });
    expect(usePlaygroundStore.getState().history[0].outputs[0].saved_to_library).toBe(false);
    await waitFor(() => expect(category).toBeEnabled());
  });

  it('does not offer the image asset-library action for video outputs', () => {
    usePlaygroundStore.setState({ history: [videoGeneration] });
    const card = renderWithIntl(
      <ResultCard generation={videoGeneration} />,
      { locale: 'en' },
    );
    expect(screen.queryByRole('combobox', { name: 'Save to library' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Use as reference' })).not.toBeInTheDocument();
    card.unmount();

    renderWithIntl(
      <DetailPanel
        generation={videoGeneration}
        allGenerations={[videoGeneration]}
        focusOutputId="video-output"
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
      { locale: 'en' },
    );
    expect(screen.queryByRole('combobox', { name: 'Save to library' })).not.toBeInTheDocument();
    expect(playgroundApi.saveToLibrary).not.toHaveBeenCalled();
  });
});
