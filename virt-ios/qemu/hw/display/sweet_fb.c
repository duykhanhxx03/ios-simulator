/*
 * Linear framebuffer display for virt-ios.
 *
 * The ios-sweet XNU kernel draws (console and the copied iOS frames, patches
 * 0003/0046/0056) into a fixed a8r8g8b8 framebuffer that the phone's
 * bootloader leaves at 0x9c000000. On virt that address is plain guest RAM;
 * this device just shows that RAM as a QEMU graphic console, zero-copy.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "hw/core/sysbus.h"
#include "hw/core/qdev-properties.h"
#include "ui/console.h"
#include "system/address-spaces.h"
#include "exec/cpu-common.h"

#define TYPE_SWEET_FB "sweet-fb"
OBJECT_DECLARE_SIMPLE_TYPE(SweetFBState, SWEET_FB)

struct SweetFBState {
    SysBusDevice parent_obj;
    QemuConsole *con;
    uint64_t base;
    uint32_t width, height, stride;
    void *map;
    hwaddr map_len;
};

static void sweet_fb_update(void *opaque)
{
    SweetFBState *s = opaque;

    if (!s->map) {
        hwaddr len = (hwaddr)s->stride * s->height;
        s->map = cpu_physical_memory_map(s->base, &len, false);
        if (!s->map || len < (hwaddr)s->stride * s->height) {
            if (s->map) {
                cpu_physical_memory_unmap(s->map, len, false, 0);
            }
            s->map = NULL;
            return;
        }
        s->map_len = len;
        DisplaySurface *surf = qemu_create_displaysurface_from(
            s->width, s->height, PIXMAN_x8r8g8b8, s->stride, s->map);
        dpy_gfx_replace_surface(s->con, surf);
    }
    dpy_gfx_update_full(s->con);
}

static void sweet_fb_invalidate(void *opaque)
{
    SweetFBState *s = opaque;
    if (s->map) {
        dpy_gfx_update_full(s->con);
    }
}

static const GraphicHwOps sweet_fb_ops = {
    .gfx_update = sweet_fb_update,
    .invalidate = sweet_fb_invalidate,
};

static void sweet_fb_realize(DeviceState *dev, Error **errp)
{
    SweetFBState *s = SWEET_FB(dev);
    if (!s->width || !s->height) {
        error_setg(errp, "sweet-fb: width/height must be set");
        return;
    }
    if (!s->stride) {
        s->stride = s->width * 4;
    }
    s->con = graphic_console_init(dev, 0, &sweet_fb_ops, s);
    qemu_console_resize(s->con, s->width, s->height);
}

static const Property sweet_fb_props[] = {
    DEFINE_PROP_UINT64("base", SweetFBState, base, 0x9c000000),
    DEFINE_PROP_UINT32("width", SweetFBState, width, 1080),
    DEFINE_PROP_UINT32("height", SweetFBState, height, 2400),
    DEFINE_PROP_UINT32("stride", SweetFBState, stride, 0),
};

static void sweet_fb_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = sweet_fb_realize;
    device_class_set_props(dc, sweet_fb_props);
    dc->desc = "Linear a8r8g8b8 framebuffer in guest RAM (ios-sweet)";
    set_bit(DEVICE_CATEGORY_DISPLAY, dc->categories);
}

static const TypeInfo sweet_fb_info = {
    .name = TYPE_SWEET_FB,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(SweetFBState),
    .class_init = sweet_fb_class_init,
};

static void sweet_fb_register(void)
{
    type_register_static(&sweet_fb_info);
}
type_init(sweet_fb_register)
