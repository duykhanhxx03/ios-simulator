// Spike: can Windows give reims-vgpu what Linux's mmap gives it?
//
//  1. "Guest RAM" is a pagefile-backed section (what QEMU's RAM would become on Windows).
//  2. A view of guest pages in any order, at 16 KiB (iOS guest page) and 4 KiB granularity:
//     a placeholder split into pieces, each replaced by a view of the section
//     (VirtualAlloc2 + MapViewOfFile3 + MEM_REPLACE_PLACEHOLDER) - Mach's remap / Linux's mmap.
//  3. That multi-view range imported into Vulkan with VK_EXT_external_memory_host on the GPU,
//     GPU reads and writes through it, checked against the guest's own view.
//
// Build (x64 Native Tools prompt):  cl /nologo /O2 /W3 spike.c /I include onecore.lib
#define WIN32_LEAN_AND_MEAN
#define VK_NO_PROTOTYPES
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <vulkan/vulkan.h>

#define CHECK(c, ...) do { if (!(c)) { printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); exit(1); } } while (0)

static HANDLE section;
static uint8_t *guest;                 // the guest's own view of its RAM
static const SIZE_T GUEST_SIZE = 64u << 20;

// A view of `n` pages of `piece` bytes at a fresh placeholder; piece j shows guest page perm[j].
static uint8_t *alias_view(SIZE_T piece, int n, const int *perm)
{
    SIZE_T size = piece * n;
    uint8_t *base = VirtualAlloc2(NULL, NULL, size, MEM_RESERVE | MEM_RESERVE_PLACEHOLDER,
                                  PAGE_NOACCESS, NULL, 0);
    CHECK(base, "VirtualAlloc2 placeholder: %lu", GetLastError());
    for (int j = 0; j < n - 1; j++)    // split into n placeholders of one piece each
        CHECK(VirtualFree(base + piece * j, piece, MEM_RELEASE | MEM_PRESERVE_PLACEHOLDER),
              "split at %d: %lu", j, GetLastError());
    for (int j = 0; j < n; j++) {
        void *v = MapViewOfFile3(section, GetCurrentProcess(), base + piece * j,
                                 (ULONG64)perm[j] * piece, piece, MEM_REPLACE_PLACEHOLDER,
                                 PAGE_READWRITE, NULL, 0);
        CHECK(v == base + piece * j, "MapViewOfFile3 piece %d (offset 0x%llx): %lu", j,
              (unsigned long long)perm[j] * piece, GetLastError());
    }
    return base;
}

static void test_alias(SIZE_T piece)
{
    enum { N = 16 };
    int perm[N];
    for (int j = 0; j < N; j++) perm[j] = (j * 7 + 3) % N + 1;   // scrambled, never 64K-aligned order
    for (int p = 0; p <= N; p++) memset(guest + piece * p, 0x40 + p, piece);
    uint8_t *v = alias_view(piece, N, perm);
    for (int j = 0; j < N; j++)
        CHECK(v[piece * j] == 0x40 + perm[j] && v[piece * j + piece - 1] == 0x40 + perm[j],
              "alias read piece %d", j);
    v[piece * 5 + 17] = 0xEE;                                   // write through the alias...
    CHECK(guest[piece * perm[5] + 17] == 0xEE, "alias write not visible in guest view");
    printf("  alias %5zu-byte pieces: %d scrambled views OK (reads, writes shared)\n", piece, N);
}

// ---- Vulkan ----------------------------------------------------------------------------------
static PFN_vkGetInstanceProcAddr gipa;
#define IFN(name) PFN_##name name = (PFN_##name)gipa(inst, #name)

int main(void)
{
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    printf("page %lu, allocation granularity %lu\n", si.dwPageSize, si.dwAllocationGranularity);

    section = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE | SEC_COMMIT,
                                 (DWORD)(GUEST_SIZE >> 32), (DWORD)GUEST_SIZE, NULL);
    CHECK(section, "CreateFileMapping: %lu", GetLastError());
    guest = MapViewOfFile(section, FILE_MAP_ALL_ACCESS, 0, 0, GUEST_SIZE);
    CHECK(guest, "MapViewOfFile: %lu", GetLastError());

    printf("[1] placeholder aliasing\n");
    test_alias(16384);
    test_alias(4096);

    printf("[2] Vulkan host-pointer import of a 16-view alias\n");
    HMODULE lib = LoadLibraryA("vulkan-1.dll");
    CHECK(lib, "vulkan-1.dll");
    gipa = (PFN_vkGetInstanceProcAddr)GetProcAddress(lib, "vkGetInstanceProcAddr");
    VkInstance inst = NULL;
    IFN(vkCreateInstance);
    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "hostvm-spike", 1, NULL, 0,
                              VK_API_VERSION_1_3 };
    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app };
    CHECK(vkCreateInstance(&ici, NULL, &inst) == VK_SUCCESS, "vkCreateInstance");
    IFN(vkEnumeratePhysicalDevices); IFN(vkGetPhysicalDeviceProperties2);
    IFN(vkGetPhysicalDeviceQueueFamilyProperties); IFN(vkCreateDevice); IFN(vkGetDeviceProcAddr);
    IFN(vkGetPhysicalDeviceMemoryProperties);

    VkPhysicalDevice pds[8]; uint32_t npd = 8;
    vkEnumeratePhysicalDevices(inst, &npd, pds);
    VkPhysicalDevice pd = NULL;
    VkPhysicalDeviceExternalMemoryHostPropertiesEXT hostp =
        { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT };
    VkPhysicalDeviceProperties2 props = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &hostp };
    for (uint32_t i = 0; i < npd; i++) {
        vkGetPhysicalDeviceProperties2(pds[i], &props);
        if (props.properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) { pd = pds[i]; break; }
    }
    CHECK(pd, "no discrete GPU");
    printf("  device %s, minImportedHostPointerAlignment 0x%llx\n", props.properties.deviceName,
           (unsigned long long)hostp.minImportedHostPointerAlignment);

    VkQueueFamilyProperties qf[16]; uint32_t nqf = 16;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &nqf, qf);
    uint32_t fam = 0;
    while (fam < nqf && !(qf[fam].queueFlags & VK_QUEUE_GRAPHICS_BIT)) fam++;
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, fam, 1, &prio };
    const char *exts[] = { VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME };
    VkDeviceCreateInfo dci = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qci, 0, NULL, 1, exts };
    VkDevice dev;
    CHECK(vkCreateDevice(pd, &dci, NULL, &dev) == VK_SUCCESS, "vkCreateDevice");
#define DFN(name) PFN_##name name = (PFN_##name)vkGetDeviceProcAddr(dev, #name)
    DFN(vkGetMemoryHostPointerPropertiesEXT); DFN(vkCreateBuffer); DFN(vkAllocateMemory);
    DFN(vkBindBufferMemory); DFN(vkGetBufferMemoryRequirements); DFN(vkGetDeviceQueue);
    DFN(vkCreateCommandPool); DFN(vkAllocateCommandBuffers); DFN(vkBeginCommandBuffer);
    DFN(vkCmdFillBuffer); DFN(vkCmdCopyBuffer); DFN(vkEndCommandBuffer); DFN(vkQueueSubmit);
    DFN(vkQueueWaitIdle); DFN(vkCmdPipelineBarrier);

    enum { N = 16 };
    const SIZE_T piece = 16384, size = piece * N;
    int perm[N];
    for (int j = 0; j < N; j++) perm[j] = (j * 5 + 2) % N + 32;   // guest pages 32..47, scrambled
    for (int p = 32; p < 48; p++) memset(guest + piece * p, p, piece);
    uint8_t *view = alias_view(piece, N, perm);

    VkMemoryHostPointerPropertiesEXT hpp = { VK_STRUCTURE_TYPE_MEMORY_HOST_POINTER_PROPERTIES_EXT };
    VkResult r = vkGetMemoryHostPointerPropertiesEXT(dev,
        VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, view, &hpp);
    CHECK(r == VK_SUCCESS && hpp.memoryTypeBits, "host pointer properties: %d, bits 0x%x", r, hpp.memoryTypeBits);
    printf("  host pointer import allowed, memoryTypeBits 0x%x\n", hpp.memoryTypeBits);

    // A buffer over the multi-view alias, and one over a run of the guest's own view.
    VkExternalMemoryBufferCreateInfo ebci = { VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO, NULL,
        VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT };
    VkBufferCreateInfo bci = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, &ebci, 0, size,
        VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT, VK_SHARING_MODE_EXCLUSIVE };
    VkBuffer bview, bguest;
    CHECK(vkCreateBuffer(dev, &bci, NULL, &bview) == VK_SUCCESS, "buffer view");
    CHECK(vkCreateBuffer(dev, &bci, NULL, &bguest) == VK_SUCCESS, "buffer guest");
    uint32_t mt = 0;
    while (!(hpp.memoryTypeBits & (1u << mt))) mt++;
    VkImportMemoryHostPointerInfoEXT imp = { VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT, NULL,
        VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, view };
    VkMemoryAllocateInfo mai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &imp, size, mt };
    VkDeviceMemory mview, mguest;
    r = vkAllocateMemory(dev, &mai, NULL, &mview);
    CHECK(r == VK_SUCCESS, "import of the 16-view alias refused: %d", r);
    printf("  imported the 16-view alias (%zu KiB) as VkDeviceMemory\n", size >> 10);
    uint8_t *gsrc = guest + piece * 100;                        // guest pages 100..115
    for (SIZE_T i = 0; i < size; i++) gsrc[i] = (uint8_t)(i * 13);
    imp.pHostPointer = gsrc;
    CHECK(vkAllocateMemory(dev, &mai, NULL, &mguest) == VK_SUCCESS, "import guest run");
    CHECK(vkBindBufferMemory(dev, bview, mview, 0) == VK_SUCCESS, "bind view");
    CHECK(vkBindBufferMemory(dev, bguest, mguest, 0) == VK_SUCCESS, "bind guest");

    // GPU: fill alias piece 3 with a pattern; copy guest pages 100.. into alias pieces 8..11.
    VkCommandPoolCreateInfo cpci = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, 0, fam };
    VkCommandPool pool; vkCreateCommandPool(dev, &cpci, NULL, &pool);
    VkCommandBufferAllocateInfo cbai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool,
                                         VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1 };
    VkCommandBuffer cb; vkAllocateCommandBuffers(dev, &cbai, &cb);
    VkCommandBufferBeginInfo cbbi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    vkBeginCommandBuffer(cb, &cbbi);
    vkCmdFillBuffer(cb, bview, piece * 3, piece, 0xABCDEF01u);
    VkBufferCopy cp = { 0, piece * 8, piece * 4 };
    vkCmdCopyBuffer(cb, bguest, bview, 1, &cp);
    VkMemoryBarrier mb = { VK_STRUCTURE_TYPE_MEMORY_BARRIER, NULL, VK_ACCESS_TRANSFER_WRITE_BIT,
                           VK_ACCESS_HOST_READ_BIT };
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &mb,
                         0, NULL, 0, NULL);
    vkEndCommandBuffer(cb);
    VkQueue q; vkGetDeviceQueue(dev, fam, 0, &q);
    VkSubmitInfo sub = { VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb };
    LARGE_INTEGER f, t0, t1; QueryPerformanceFrequency(&f); QueryPerformanceCounter(&t0);
    CHECK(vkQueueSubmit(q, 1, &sub, VK_NULL_HANDLE) == VK_SUCCESS, "submit");
    vkQueueWaitIdle(q);
    QueryPerformanceCounter(&t1);

    // Check through the GUEST's own view: the GPU's writes must land in the guest's pages.
    uint32_t want = 0xABCDEF01u, got;
    memcpy(&got, guest + piece * perm[3], 4);
    CHECK(got == want, "GPU fill not visible in guest page %d: 0x%08x", perm[3], got);
    memcpy(&got, guest + piece * perm[3] + piece - 4, 4);
    CHECK(got == want, "GPU fill tail");
    for (int k = 0; k < 4; k++)
        CHECK(memcmp(guest + piece * perm[8 + k], gsrc + piece * k, piece) == 0,
              "GPU copy into alias piece %d (guest page %d) wrong", 8 + k, perm[8 + k]);
    CHECK(guest[piece * perm[0]] == perm[0], "untouched piece changed");
    printf("  GPU fill + copy through the alias landed in the scrambled guest pages (%.2f ms)\n",
           (t1.QuadPart - t0.QuadPart) * 1000.0 / f.QuadPart);
    printf("ALL OK\n");
    return 0;
}
