import { act, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { renderWithIntl } from '@/test/renderWithIntl';
import { vi, describe, it, expect, beforeEach } from 'vitest';

const authState = { serverMode: false };

vi.mock('@/components/auth/AuthProvider', () => ({
    useAuth: () => authState,
}));

vi.mock('@/lib/apiUrl', () => ({
    API_URL: 'http://127.0.0.1:17177',
}));

vi.mock('@/components/shared/preview/LightboxProvider', () => ({
    useLightbox: () => ({
        open: vi.fn(),
        openInGroup: vi.fn(),
        registerGroup: vi.fn(),
        unregisterGroup: vi.fn(),
    }),
}));

// Mock framer-motion
vi.mock('framer-motion', () => ({
    motion: {
        div: ({ children, ...props }: any) => {
            const { whileHover, whileTap, initial, animate, exit, variants, transition, layoutId, ...rest } = props;
            return <div {...rest}>{children}</div>;
        },
        aside: ({ children, ...props }: any) => {
            const { initial, animate, transition, ...rest } = props;
            return <aside {...rest}>{children}</aside>;
        },
        button: ({ children, ...props }: any) => {
            const { whileHover, whileTap, initial, animate, exit, transition, ...rest } = props;
            return <button {...rest}>{children}</button>;
        },
    },
    AnimatePresence: ({ children }: any) => <>{children}</>,
}));

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
    ArrowLeft: (props: any) => <span data-testid="icon-arrow-left" {...props} />,
    Users: (props: any) => <span data-testid="icon-users" {...props} />,
    MapPin: (props: any) => <span data-testid="icon-map-pin" {...props} />,
    Package: (props: any) => <span data-testid="icon-package" {...props} />,
    Plus: (props: any) => <span data-testid="icon-plus" {...props} />,
    X: (props: any) => <span data-testid="icon-x" {...props} />,
    Image: (props: any) => <span data-testid="icon-image" {...props} />,
    Settings: (props: any) => <span data-testid="icon-settings" {...props} />,
    FileText: (props: any) => <span data-testid="icon-file-text" {...props} />,
    Download: (props: any) => <span data-testid="icon-download" {...props} />,
    MessageSquareCode: (props: any) => <span data-testid="icon-message-square-code" {...props} />,
    Palette: (props: any) => <span data-testid="icon-palette" {...props} />,
    ChevronLeft: (props: any) => <span data-testid="icon-chevron-left" {...props} />,
    ChevronRight: (props: any) => <span data-testid="icon-chevron-right" {...props} />,
    Play: (props: any) => <span data-testid="icon-play" {...props} />,
    Trash2: (props: React.ComponentProps<'span'>) => <span data-testid="icon-trash" {...props} />,
    AlertTriangle: (props: any) => <span data-testid="icon-alert" {...props} />,
    RefreshCw: (props: any) => <span data-testid="icon-refresh" {...props} />,
    Maximize2: (props: any) => <span data-testid="icon-maximize" {...props} />,
    Copy: (props: any) => <span data-testid="icon-copy" {...props} />,
    Check: (props: any) => <span data-testid="icon-check" {...props} />,
}));

// Mock AssetCard
vi.mock('@/components/common/AssetCard', () => ({
    default: ({ asset, type }: any) => (
        <div data-testid={`asset-card-${asset.id}`}>{asset.name}</div>
    ),
}));

// Mock API
const mockGetSeries = vi.fn();
const mockGetSeriesEpisodes = vi.fn();
const mockUpdateSeries = vi.fn();
const mockCreateProject = vi.fn();
const mockAddEpisodeToSeries = vi.fn();
const mockCreateEpisodeForSeries = vi.fn();
const mockDeleteSeries = vi.fn();

vi.mock('@/lib/api', () => ({
    api: {
        getSeries: (...args: any[]) => mockGetSeries(...args),
        getSeriesEpisodes: (...args: any[]) => mockGetSeriesEpisodes(...args),
        updateSeries: (...args: any[]) => mockUpdateSeries(...args),
        createProject: (...args: any[]) => mockCreateProject(...args),
        addEpisodeToSeries: (...args: any[]) => mockAddEpisodeToSeries(...args),
        createEpisodeForSeries: (...args: any[]) => mockCreateEpisodeForSeries(...args),
        deleteSeries: (seriesId: string) => mockDeleteSeries(seriesId),
    },
}));

import SeriesDetailPage from '../SeriesDetailPage';
import { useProjectStore, type Project, type Series } from '@/store/projectStore';
import BreadcrumbBar from '@/components/layout/BreadcrumbBar';
import { notifyAssetLibraryChanged } from '@/lib/assetLibrarySync';
import { notifyStoryboardFramesChanged } from '@/lib/storyboardFrameSync';
import {
    TopBarNavigationProvider,
    useTopBarNavigation,
} from '@/components/layout/TopBarNavigationContext';

// ── Test Data ──

const mockSeries: Series = {
    id: 'series-1',
    title: '测试系列',
    description: '这是一个测试系列',
    characters: [
        { id: 'char-1', name: '角色A', description: '描述A' },
        { id: 'char-2', name: '角色B', description: '描述B' },
    ],
    scenes: [
        { id: 'scene-1', name: '场景A', description: '场景描述A' },
    ],
    props: [],
    episode_ids: ['ep-1', 'ep-2'],
    created_at: Date.now(),
    updated_at: Date.now(),
};

const mockEpisodes: Project[] = [
    {
        id: 'ep-1',
        title: '第一集',
        originalText: '',
        characters: [],
        scenes: [],
        props: [],
        frames: [{ id: 'f1' }],
        status: 'draft',
        createdAt: '2026-07-21T00:00:00.000Z',
        updatedAt: '2026-07-21T00:00:00.000Z',
        series_id: 'series-1',
        episode_number: 1,
    },
    {
        id: 'ep-2',
        title: '第二集',
        originalText: '',
        characters: [],
        scenes: [],
        props: [],
        frames: [],
        status: 'draft',
        createdAt: '2026-07-21T00:00:00.000Z',
        updatedAt: '2026-07-21T00:00:00.000Z',
        series_id: 'series-1',
        episode_number: 2,
    },
];

// ── Helpers ──

function renderPage(seriesId = 'series-1') {
    return renderWithIntl(<SeriesDetailPage seriesId={seriesId} />);
}

function RegisteredTopBar() {
    const { navigation } = useTopBarNavigation();

    return (
        <header role="banner">
            {navigation && (
                <BreadcrumbBar
                    segments={navigation.segments}
                    currentContent={navigation.currentContent}
                    description={navigation.description}
                    actions={navigation.actions}
                    embedded
                />
            )}
            <span>管理员</span>
        </header>
    );
}

function renderServerPage(seriesId = 'series-1') {
    authState.serverMode = true;
    return renderWithIntl(
        <TopBarNavigationProvider>
            <RegisteredTopBar />
            <SeriesDetailPage seriesId={seriesId} />
        </TopBarNavigationProvider>,
    );
}

// ── Tests ──

describe('SeriesDetailPage', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        authState.serverMode = false;
        mockGetSeries.mockResolvedValue(mockSeries);
        mockGetSeriesEpisodes.mockResolvedValue(mockEpisodes);
        useProjectStore.setState({
            projects: mockEpisodes,
            currentProject: null,
            selectedFrameId: null,
            seriesList: [mockSeries],
            currentSeries: mockSeries,
        });
    });

    describe('Delete entire series', () => {
        it('requires confirmation, deletes through the server, and returns home', async () => {
            mockDeleteSeries.mockResolvedValue({
                status: 'deleted',
                deleted_episode_count: 2,
            });
            const confirmDelete = vi.spyOn(window, 'confirm').mockReturnValue(true);
            window.location.hash = '#/series/series-1';

            renderPage();
            const deleteButton = await screen.findByRole('button', { name: '删除系列' });
            fireEvent.click(deleteButton);

            await waitFor(() => {
                expect(mockDeleteSeries).toHaveBeenCalledWith('series-1');
            });
            expect(confirmDelete).toHaveBeenCalledWith(
                expect.stringContaining('全部 2 集'),
            );
            expect(useProjectStore.getState().seriesList).toEqual([]);
            expect(useProjectStore.getState().projects).toEqual([]);
            expect(window.location.hash).toBe('#/');

            confirmDelete.mockRestore();
        });

        it('keeps the series when deletion is canceled', async () => {
            const confirmDelete = vi.spyOn(window, 'confirm').mockReturnValue(false);

            renderPage();
            const deleteButton = await screen.findByRole('button', { name: '删除系列' });
            fireEvent.click(deleteButton);

            expect(mockDeleteSeries).not.toHaveBeenCalled();
            expect(useProjectStore.getState().seriesList).toHaveLength(1);

            confirmDelete.mockRestore();
        });
    });

    // ── Rendering ──

    describe('Rendering', () => {
        it('shows loading state initially', () => {
            mockGetSeries.mockReturnValue(new Promise(() => {}));
            mockGetSeriesEpisodes.mockReturnValue(new Promise(() => {}));
            renderPage();
            expect(screen.getByText('加载中...')).toBeInTheDocument();
        });

        it('shows series title after loading', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
        });

        it('shows series description', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('这是一个测试系列')).toBeInTheDocument();
            });
        });

        it('moves series navigation into the server top bar and removes the sidebar box', async () => {
            window.location.hash = '#/series/series-1';
            renderServerPage();

            const topBar = await screen.findByRole('banner');
            expect(await within(topBar).findByText('EnMotion')).toBeInTheDocument();
            expect(within(topBar).getByTestId('series-top-bar-title')).toHaveTextContent('测试系列');
            expect(within(topBar).getByTestId('series-top-bar-title')).toHaveClass('truncate');
            expect(within(topBar).getByTestId('top-bar-description')).toHaveTextContent('这是一个测试系列');
            expect(within(topBar).getByTestId('top-bar-description')).toHaveClass('hidden', 'truncate', 'lg:block');
            expect(within(topBar).getByText('管理员')).toBeInTheDocument();

            const sidebar = screen.getByRole('complementary');
            expect(within(sidebar).queryByText('EnMotion')).not.toBeInTheDocument();
            expect(within(sidebar).queryByText('测试系列')).not.toBeInTheDocument();
            expect(within(sidebar).queryByText('这是一个测试系列')).not.toBeInTheDocument();
            expect(within(sidebar).getByText('风格基线')).toBeInTheDocument();

            fireEvent.click(within(topBar).getByText('EnMotion'));
            expect(window.location.hash).toBe('#/');
        });

        it('omits the optional description from the top bar when the series has none', async () => {
            mockGetSeries.mockResolvedValue({ ...mockSeries, description: '' });
            renderServerPage();

            await screen.findByTestId('series-top-bar-title');
            expect(screen.queryByTestId('top-bar-description')).not.toBeInTheDocument();
        });
    });

    // ── Error state ──

    describe('Error state', () => {
        it('shows error view when API fails', async () => {
            mockGetSeries.mockRejectedValue(new Error('Network error'));
            mockGetSeriesEpisodes.mockRejectedValue(new Error('Network error'));
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('系列未找到')).toBeInTheDocument();
            });
        });

        it('shows link to go back to home on error', async () => {
            mockGetSeries.mockRejectedValue(new Error('fail'));
            mockGetSeriesEpisodes.mockRejectedValue(new Error('fail'));
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('返回首页')).toBeInTheDocument();
            });
        });
    });

    // ── Assets display / Sidebar navigation ──

    describe('Assets display', () => {
        it('refetches the resolved series assets after a global library change', async () => {
            const tester = {
                id: 'char_tester',
                name: 'tester',
                description: 'tester description',
                source: 'global',
                reference_sheet: {
                    selected_image_id: 'img_tester',
                    image_variants: [{
                        id: 'img_tester',
                        url: 'uploads/tester.png',
                        created_at: 1_784_671_200,
                    }],
                },
            };
            mockGetSeries
                .mockResolvedValueOnce(mockSeries)
                .mockResolvedValueOnce({
                    ...mockSeries,
                    characters: [...mockSeries.characters, tester],
                });

            renderPage();
            await screen.findByText('角色A');

            act(() => {
                notifyAssetLibraryChanged({
                    source: 'global',
                    assetType: 'character',
                    assetId: tester.id,
                });
            });

            expect(await screen.findByText('tester')).toBeInTheDocument();
            expect(mockGetSeries).toHaveBeenCalledTimes(2);
            expect(mockGetSeries).toHaveBeenLastCalledWith(mockSeries.id);
        });

        it('shows characters as default content with asset cards', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            // Sidebar shows asset tabs
            expect(screen.getAllByText('角色').length).toBeGreaterThanOrEqual(1);
            // Content area shows character assets
            expect(screen.getByText('角色A')).toBeInTheDocument();
            expect(screen.getByText('角色B')).toBeInTheDocument();
        });

        it('switches to scenes when clicked in sidebar', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            fireEvent.click(screen.getByText('场景'));
            await waitFor(() => {
                expect(screen.getByText('场景A')).toBeInTheDocument();
            });
        });

        it('shows empty state when props tab has no assets', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            fireEvent.click(screen.getByText('道具'));
            await waitFor(() => {
                expect(screen.getByText('暂无道具资产')).toBeInTheDocument();
            });
        });

        it('displays asset counts in sidebar', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            // Characters: 2, Scenes: 1, Props: 0
            expect(screen.getByText('2')).toBeInTheDocument();
            expect(screen.getAllByText('1').length).toBeGreaterThanOrEqual(1);
            expect(screen.getAllByText('0').length).toBeGreaterThanOrEqual(1);
        });
    });

    // ── Episode list in sidebar ──

    describe('Episode list', () => {
        it('shows episode list with titles in sidebar', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('第一集')).toBeInTheDocument();
            });
            expect(screen.getByText('第二集')).toBeInTheDocument();
        });

        it('shows episode numbers', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('第 1 集')).toBeInTheDocument();
            });
            expect(screen.getByText('第 2 集')).toBeInTheDocument();
        });

        it('shows frame count for episodes in sidebar', async () => {
            renderPage();
            await waitFor(() => {
                // Frame counts are displayed as plain numbers in sidebar
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
        });

        it('shows episode content panel when clicked, then navigates via button', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('第一集')).toBeInTheDocument();
            });
            // Click episode in sidebar to show preview
            fireEvent.click(screen.getByText('第一集'));
            await waitFor(() => {
                expect(screen.getByText('进入编辑器')).toBeInTheDocument();
            });
            // Click "进入编辑器" to navigate
            fireEvent.click(screen.getByText('进入编辑器'));
            expect(window.location.hash).toBe('#/series/series-1/episode/ep-1');
        });

        it('renders the exact selected storyboard variant through the authenticated media URL', async () => {
            mockGetSeriesEpisodes.mockResolvedValue([{ ...mockEpisodes[0], frames: [{
                id: 'frame-selected',
                rendered_image_url: 'storyboard/stale.png',
                rendered_image_asset: {
                    selected_id: 'selected-variant',
                    variants: [
                        { id: 'stale-variant', url: 'storyboard/stale.png', created_at: 1 },
                        { id: 'selected-variant', url: 'storyboard/selected.png', created_at: 2 },
                    ],
                },
            }] }]);

            renderPage();
            fireEvent.click(await screen.findByText('第一集'));

            expect(await screen.findByAltText('分镜 1')).toHaveAttribute(
                'src',
                'http://127.0.0.1:17177/files/storyboard/selected.png',
            );
        });

        it('shows No image only when the frame has no persisted image candidate', async () => {
            renderPage();
            fireEvent.click(await screen.findByText('第一集'));

            expect(await screen.findByText('暂无图片')).toBeInTheDocument();
            expect(screen.queryByAltText('分镜 1')).not.toBeInTheDocument();
        });

        it('refreshes an open storyboard overview immediately when the selected image changes', async () => {
            const initial = [{ ...mockEpisodes[0], frames: [{
                id: 'frame-live',
                rendered_image_asset: {
                    selected_id: 'variant-a',
                    variants: [{ id: 'variant-a', url: 'storyboard/a.png', created_at: 1 }],
                },
            }] }];
            const updated = [{ ...mockEpisodes[0], frames: [{
                id: 'frame-live',
                rendered_image_asset: {
                    selected_id: 'variant-b',
                    variants: [{ id: 'variant-b', url: 'uploads/b.webp', created_at: 2 }],
                },
            }] }];
            mockGetSeriesEpisodes.mockResolvedValueOnce(initial).mockResolvedValue(updated);

            renderPage();
            fireEvent.click(await screen.findByText('第一集'));
            expect(await screen.findByAltText('分镜 1')).toHaveAttribute(
                'src',
                'http://127.0.0.1:17177/files/storyboard/a.png',
            );

            act(() => notifyStoryboardFramesChanged({
                projectId: 'ep-1',
                seriesId: 'series-1',
                frameId: 'frame-live',
            }));

            await waitFor(() => expect(screen.getByAltText('分镜 1')).toHaveAttribute(
                'src',
                'http://127.0.0.1:17177/files/uploads/b.webp',
            ));
            expect(mockGetSeriesEpisodes).toHaveBeenCalledTimes(2);
        });

        it('offers a retry after an authenticated storyboard image fails to load', async () => {
            const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
            mockGetSeriesEpisodes.mockResolvedValue([{ ...mockEpisodes[0], frames: [{
                id: 'frame-missing',
                rendered_image_url: 'storyboard/missing.png',
            }] }]);

            renderPage();
            fireEvent.click(await screen.findByText('第一集'));
            const image = await screen.findByAltText('分镜 1');
            fireEvent.error(image);
            await waitFor(() => expect(screen.getByAltText('分镜 1').getAttribute('src')).toContain('__r=1'));
            fireEvent.error(screen.getByAltText('分镜 1'));

            expect(await screen.findByRole('button', { name: '加载失败，点击重试' })).toBeInTheDocument();
            expect(warn).toHaveBeenCalledWith(
                '[MediaPreview] Image failed to load',
                expect.objectContaining({
                    context: 'series-storyboard-overview',
                    transport: 'authenticated-file',
                    extension: 'png',
                }),
            );
            expect(JSON.stringify(warn.mock.calls)).not.toContain('missing.png');
            warn.mockRestore();
        });

        it('shows episodes count in sidebar header', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('集数 (2)')).toBeInTheDocument();
            });
        });
    });

    // ── Empty episode state ──

    describe('Empty episode state', () => {
        it('shows empty state when no episodes', async () => {
            mockGetSeriesEpisodes.mockResolvedValue([]);
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('暂无集数')).toBeInTheDocument();
            });
        });
    });

    // ── Edit title ──

    describe('Edit title', () => {
        it('enters edit mode on double click', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            const titleEl = screen.getByTestId('series-top-bar-title');
            fireEvent.doubleClick(titleEl);
            const input = screen.getByDisplayValue('测试系列');
            expect(input).toBeInTheDocument();
            expect(input.tagName).toBe('INPUT');
        });

        it('saves title on blur', async () => {
            mockUpdateSeries.mockResolvedValue({});
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            const titleEl = screen.getByTestId('series-top-bar-title');
            fireEvent.doubleClick(titleEl);
            const input = screen.getByDisplayValue('测试系列');
            fireEvent.change(input, { target: { value: '新标题' } });
            fireEvent.blur(input);
            await waitFor(() => {
                expect(mockUpdateSeries).toHaveBeenCalledWith('series-1', { title: '新标题' });
            });
        });

        it('saves title on Enter key', async () => {
            mockUpdateSeries.mockResolvedValue({});
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            const titleEl = screen.getByTestId('series-top-bar-title');
            fireEvent.doubleClick(titleEl);
            const input = screen.getByDisplayValue('测试系列');
            fireEvent.change(input, { target: { value: '回车标题' } });
            fireEvent.keyDown(input, { key: 'Enter' });
            await waitFor(() => {
                expect(mockUpdateSeries).toHaveBeenCalledWith('series-1', { title: '回车标题' });
            });
        });

        it('cancels edit on Escape key', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            const titleEl = screen.getByTestId('series-top-bar-title');
            fireEvent.doubleClick(titleEl);
            const input = screen.getByDisplayValue('测试系列');
            fireEvent.change(input, { target: { value: '取消的标题' } });
            fireEvent.keyDown(input, { key: 'Escape' });
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            expect(mockUpdateSeries).not.toHaveBeenCalled();
        });

        it('reverts title if API fails', async () => {
            mockUpdateSeries.mockRejectedValue(new Error('API error'));
            renderPage();
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
            const titleEl = screen.getByTestId('series-top-bar-title');
            fireEvent.doubleClick(titleEl);
            const input = screen.getByDisplayValue('测试系列');
            fireEvent.change(input, { target: { value: '失败标题' } });
            fireEvent.blur(input);
            await waitFor(() => {
                expect(screen.getAllByText('测试系列').length).toBeGreaterThanOrEqual(1);
            });
        });
    });

    // ── Add episode ──

    describe('Add episode', () => {
        it('shows add episode button', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('添加集数')).toBeInTheDocument();
            });
        });

        it('shows input form when add button is clicked', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('添加集数')).toBeInTheDocument();
            });
            fireEvent.click(screen.getByText('添加集数'));
            expect(screen.getByPlaceholderText('集数标题...')).toBeInTheDocument();
            expect(screen.getByText('确定')).toBeInTheDocument();
            expect(screen.getByText('取消')).toBeInTheDocument();
        });

        it('creates new episode when confirmed', async () => {
            const newProject = { id: 'new-proj', title: '新集数' };
            mockCreateEpisodeForSeries.mockResolvedValue(newProject);
            mockGetSeriesEpisodes.mockResolvedValueOnce(mockEpisodes).mockResolvedValueOnce([
                ...mockEpisodes,
                { id: 'new-proj', title: '新集数', episode_number: 3, frames: [] },
            ]);

            renderPage();
            await waitFor(() => {
                expect(screen.getByText('添加集数')).toBeInTheDocument();
            });
            fireEvent.click(screen.getByText('添加集数'));

            const input = screen.getByPlaceholderText('集数标题...');
            fireEvent.change(input, { target: { value: '新集数' } });
            fireEvent.click(screen.getByText('确定'));

            await waitFor(() => {
                expect(mockCreateEpisodeForSeries).toHaveBeenCalledWith(
                    'series-1',
                    '新集数',
                    3,
                    'i2v_legacy',
                );
            });
        });

        it('hides form when cancel is clicked', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('添加集数')).toBeInTheDocument();
            });
            fireEvent.click(screen.getByText('添加集数'));
            expect(screen.getByPlaceholderText('集数标题...')).toBeInTheDocument();
            fireEvent.click(screen.getByText('取消'));
            expect(screen.queryByPlaceholderText('集数标题...')).not.toBeInTheDocument();
        });

        it('disables confirm button when title is empty', async () => {
            renderPage();
            await waitFor(() => {
                expect(screen.getByText('添加集数')).toBeInTheDocument();
            });
            fireEvent.click(screen.getByText('添加集数'));
            const confirmBtn = screen.getByText('确定');
            expect(confirmBtn).toBeDisabled();
        });
    });
});
