#import <Foundation/Foundation.h>
#import <IOKit/IOKitLib.h>
#import <IOKit/IOCFPlugIn.h>
#import <IOKit/usb/IOUSBLib.h>
#import <IOKit/hid/IOHIDManager.h>
#include <stdio.h>
#include <time.h>

static IOHIDManagerRef manager;
static IOHIDDeviceRef device;
static IOUSBDeviceInterface **usb;
static IOUSBInterfaceInterface **game;
static CFRunLoopSourceRef gameSource;
static UInt8 gamePipe;
static uint8_t gameBuffer[64];
static uint64_t gameReads, validGameReads;
static BOOL closing;
static uint8_t reportBuffer[1024], queue[256][64];
static unsigned head, tail;
static BOOL removed;
static char lastError[256];
static double lastHandshake;
static double clockSeconds(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static void fail(const char *s, IOReturn r) {snprintf(lastError,sizeof(lastError),"%s: 0x%08x",s,r);}
const char *kp_error(void) {return lastError;}
static int number(io_registry_entry_t s, CFStringRef key) {
  CFTypeRef o=IORegistryEntryCreateCFProperty(s,key,NULL,0);int n=0;
  if(o && CFGetTypeID(o)==CFNumberGetTypeID())CFNumberGetValue(o,kCFNumberIntType,&n);
  if(o)CFRelease(o);return n;
}
static BOOL ours(io_registry_entry_t s, CFStringRef key) {
  CFTypeRef o=IORegistryEntryCreateCFProperty(s,key,NULL,0);
  BOOL yes=o && CFGetTypeID(o)==CFStringGetTypeID() && CFStringHasPrefix(o,CFSTR("BTP-KP20D"));
  if(o)CFRelease(o);return yes;
}
static void receive(void *ctx, IOReturn result, void *sender, IOHIDReportType type,
                    uint32_t reportID, uint8_t *data, CFIndex size) {
  if(result || reportID!=2 || size!=64)return;
  if(tail-head>=256){removed=YES;fail("input queue overflow",kIOReturnOverrun);return;}
  memcpy(queue[tail++%256],data,64);
}
static void removeDevice(void *ctx, IOReturn result, void *sender, IOHIDDeviceRef gone) {
  if(device==gone){removed=YES;fail("physical device disconnected",kIOReturnNoDevice);}
}
static BOOL handshake(IOUSBDeviceInterface **candidate, BOOL verbose) {
  uint8_t response[40]={0};
  IOUSBDevRequest string={.bmRequestType=0x80,.bRequest=6,.wValue=0x03ee,.wIndex=0,.wLength=18,.pData=response};
  IOReturn sr=(*candidate)->DeviceRequest(candidate,&string);
  if(verbose){printf("OS_STRING result=%08x length=%u bytes=",sr,(unsigned)string.wLenDone);for(unsigned i=0;i<string.wLenDone && i<40;i++)printf("%02x",response[i]);puts("");fflush(stdout);}
  memset(response,0,sizeof(response));
  IOUSBDevRequest compat={.bmRequestType=0xc0,.bRequest=0xee,.wValue=0,.wIndex=4,.wLength=40,.pData=response};
  IOReturn r=(*candidate)->DeviceRequest(candidate,&compat);
  if(verbose){UInt8 config=0;IOReturn cr=(*candidate)->GetConfiguration(candidate,&config);printf("OS_COMPAT result=%08x length=%u configuration=%u config_result=%08x\n",r,(unsigned)compat.wLenDone,config,cr);fflush(stdout);}
  lastHandshake=clockSeconds();
  if(r || compat.wLenDone!=40 || memcmp(response+18,"XUSB10",6)){fail("unexpected Microsoft compatibility descriptor",r);return NO;}
  return YES;
}
static void inputComplete(void *context, IOReturn result, void *count) {
  if(closing || !game)return;
  unsigned length=(unsigned)(uintptr_t)count;
  if(result==kIOReturnSuccess && length>0) {
    gameReads++;
    if(length>=20 && gameBuffer[0]==0 && gameBuffer[1]==0x14)validGameReads++;
    if(gameReads==1){printf("XINPUT_IN_ACTIVE length=%u bytes=",length);for(unsigned i=0;i<length && i<64;i++)printf("%02x",gameBuffer[i]);puts("");fflush(stdout);}
  } else if(result!=kIOReturnOverrun) {
    removed=YES;fail("XInput IN transfer",result);return;
  }
  IOReturn r=(*game)->ReadPipeAsync(game,gamePipe,gameBuffer,sizeof(gameBuffer),inputComplete,NULL);
  if(r){removed=YES;fail("rearm XInput IN",r);}
}
static void openInput(void) {
  if(!usb || game)return;
  IOUSBFindInterfaceRequest request={.bInterfaceClass=255,.bInterfaceSubClass=93,.bInterfaceProtocol=1,.bAlternateSetting=kIOUSBFindInterfaceDontCare};
  io_iterator_t it=0;IOReturn r=(*usb)->CreateInterfaceIterator(usb,&request,&it);
  if(r){fail("XInput interface iterator",r);return;}
  io_service_t s;
  while((s=IOIteratorNext(it))) {
    IOCFPlugInInterface **plugin=NULL;SInt32 score=0;
    r=IOCreatePlugInInterfaceForService(s,kIOUSBInterfaceUserClientTypeID,kIOCFPlugInInterfaceID,&plugin,&score);
    IOObjectRelease(s);
    if(r || !plugin)continue;
    IOUSBInterfaceInterface **candidate=NULL;
    HRESULT q=(*plugin)->QueryInterface(plugin,CFUUIDGetUUIDBytes(kIOUSBInterfaceInterfaceID),(LPVOID*)&candidate);
    IODestroyPlugInInterface(plugin);
    if(q || !candidate)continue;
    UInt8 index=255;(*candidate)->GetInterfaceNumber(candidate,&index);
    if(index!=0){(*candidate)->Release(candidate);continue;}
    r=(*candidate)->USBInterfaceOpen(candidate);
    if(r){fail("XInput interface open",r);(*candidate)->Release(candidate);continue;}
    UInt8 endpoints=0,pipe=0;(*candidate)->GetNumEndpoints(candidate,&endpoints);
    for(UInt8 p=1;p<=endpoints;p++) {
      UInt8 direction=0,number=0,type=0,interval=0;UInt16 packet=0;
      if(!(*candidate)->GetPipeProperties(candidate,p,&direction,&number,&type,&packet,&interval) && direction==kUSBIn && number==1 && type==kUSBInterrupt){pipe=p;break;}
    }
    if(!pipe){(*candidate)->USBInterfaceClose(candidate);(*candidate)->Release(candidate);continue;}
    CFRunLoopSourceRef source=NULL;r=(*candidate)->CreateInterfaceAsyncEventSource(candidate,&source);
    if(r || !source){(*candidate)->USBInterfaceClose(candidate);(*candidate)->Release(candidate);continue;}
    game=candidate;gamePipe=pipe;gameSource=source;CFRunLoopAddSource(CFRunLoopGetCurrent(),source,kCFRunLoopDefaultMode);
    r=(*game)->ReadPipeAsync(game,pipe,gameBuffer,sizeof(gameBuffer),inputComplete,NULL);
    if(r){removed=YES;fail("start XInput IN",r);}
    printf("XINPUT_INTERFACE_OPEN result=%08x IN_endpoint=81\n",r);fflush(stdout);break;
  }
  IOObjectRelease(it);
}
static void openUSB(io_service_t s) {
  IOCFPlugInInterface **plugin=NULL;SInt32 score=0;
  IOReturn r=IOCreatePlugInInterfaceForService(s,kIOUSBDeviceUserClientTypeID,kIOCFPlugInInterfaceID,&plugin,&score);
  if(r || !plugin){fail("USB plugin",r);return;}
  IOUSBDeviceInterface **candidate=NULL;
  HRESULT q=(*plugin)->QueryInterface(plugin,CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID),(LPVOID*)&candidate);
  IODestroyPlugInInterface(plugin);
  if(q || !candidate){fail("USB interface",(IOReturn)q);return;}
  r=(*candidate)->USBDeviceOpen(candidate);
  if(r){fail("USB open",r);(*candidate)->Release(candidate);return;}
  if(!handshake(candidate,YES)) {
    (*candidate)->USBDeviceClose(candidate);(*candidate)->Release(candidate);return;
  }
  usb=candidate; // Keep the physical USB session open until the operation ends.
}
void kp_close(void) {
  closing=YES;
  if(game){(*game)->AbortPipe(game,gamePipe);CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.02,false);(*game)->USBInterfaceClose(game);(*game)->Release(game);game=NULL;}
  if(gameSource){CFRunLoopRemoveSource(CFRunLoopGetCurrent(),gameSource,kCFRunLoopDefaultMode);CFRelease(gameSource);gameSource=NULL;}
  if(device){IOHIDDeviceUnscheduleFromRunLoop(device,CFRunLoopGetCurrent(),kCFRunLoopDefaultMode);IOHIDDeviceClose(device,0);CFRelease(device);device=NULL;}
  if(manager){IOHIDManagerUnscheduleFromRunLoop(manager,CFRunLoopGetCurrent(),kCFRunLoopDefaultMode);IOHIDManagerClose(manager,0);CFRelease(manager);manager=NULL;}
  if(usb){(*usb)->USBDeviceClose(usb);(*usb)->Release(usb);usb=NULL;}
}
int kp_open(int seconds) { @autoreleasepool {
  if(manager || device || seconds<1 || seconds>600)return -1;
  removed=NO;closing=NO;head=tail=0;lastError[0]=0;gameReads=validGameReads=0;
  manager=IOHIDManagerCreate(kCFAllocatorDefault,0);
  NSArray *matches=@[@{@"VendorID":@0x20bc,@"ProductID":@0x5158,@"PrimaryUsagePage":@255,@"PrimaryUsage":@3},
                      @{@"VendorID":@0x045e,@"ProductID":@0x028e,@"PrimaryUsagePage":@255,@"PrimaryUsage":@3}];
  IOHIDManagerSetDeviceMatchingMultiple(manager,(__bridge CFArrayRef)matches);
  IOHIDManagerRegisterDeviceRemovalCallback(manager,removeDevice,NULL);
  IOHIDManagerScheduleWithRunLoop(manager,CFRunLoopGetCurrent(),kCFRunLoopDefaultMode);
  IOReturn r=IOHIDManagerOpen(manager,0);
  if(r){fail("HID manager open",r);kp_close();return -1;}
  double deadline=clockSeconds()+seconds;
  uint64_t tried=0;
  while(clockSeconds()<deadline) {
    if(!usb) {
      io_iterator_t it=0;IOServiceGetMatchingServices(kIOMainPortDefault,IOServiceMatching("IOUSBHostDevice"),&it);io_service_t s;
      while((s=IOIteratorNext(it))) {
        uint64_t rid=0;IORegistryEntryGetRegistryEntryID(s,&rid);
        if(number(s,CFSTR("idVendor"))==0x20bc && number(s,CFSTR("idProduct"))==0x5158 && rid!=tried && ours(s,CFSTR("USB Product Name"))) {tried=rid;openUSB(s);}
        IOObjectRelease(s);
      }
      IOObjectRelease(it);
    }
    openInput();CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.01,false);
    CFSetRef set=IOHIDManagerCopyDevices(manager);
    if(set) {
      CFIndex count=CFSetGetCount(set);const void **items=calloc((size_t)count,sizeof(void*));CFSetGetValues(set,items);
      for(CFIndex i=0;i<count && !device;i++) {
        IOHIDDeviceRef d=(IOHIDDeviceRef)items[i];if(!ours(IOHIDDeviceGetService(d),CFSTR("Product")))continue;
        if(number(IOHIDDeviceGetService(d),CFSTR("VendorID"))==0x20bc && (!usb || !game || !validGameReads))continue;
        r=IOHIDDeviceOpen(d,0);
        if(r){fail("vendor HID open",r);continue;}
        device=(IOHIDDeviceRef)CFRetain(d);
        IOHIDDeviceRegisterInputReportCallback(device,reportBuffer,sizeof(reportBuffer),receive,NULL);
        IOHIDDeviceScheduleWithRunLoop(device,CFRunLoopGetCurrent(),kCFRunLoopDefaultMode);
      }
      free(items);CFRelease(set);
    }
    if(device)return 1;
  }
  if(!lastError[0])snprintf(lastError,sizeof(lastError),"No wired KP20D vendor HID interface. Replug USB directly to Mac.");
  kp_close();return 0;
}}
int kp_identity(int *vid,int *pid) {
  if(!device || removed)return -1;io_service_t s=IOHIDDeviceGetService(device);
  *vid=number(s,CFSTR("VendorID"));*pid=number(s,CFSTR("ProductID"));return 0;
}
int kp_disconnected(void) {return removed?1:0;}
uint64_t kp_input_reads(void) {return validGameReads;}
int kp_write(const uint8_t *data,int size) {
  if(!device || removed){fail("no live vendor HID",kIOReturnNoDevice);return -1;}
  if(size!=64 || data[0]!=2 || !(data[1]==0x55 || (data[1]==15 && (data[2]==1 || data[2]==2)))) {fail("unapproved report shape",kIOReturnBadArgument);return -1;}
  IOReturn r=IOHIDDeviceSetReport(device,kIOHIDReportTypeOutput,2,data,size);
  if(r){fail("HID output",r);return -1;}
  return 64;
}
int kp_read(uint8_t *data,int milliseconds) {
  if(!device || milliseconds<0 || milliseconds>5000)return -1;
  double deadline=clockSeconds()+milliseconds/1000.0;
  do {
    if(head<tail){memcpy(data,queue[head++%256],64);return 64;}
    if(removed)return -1;
    if(milliseconds==0)break;
    CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.005,false);
  }while(clockSeconds()<deadline);
  return 0;
}
void kp_pump(int milliseconds) {
  if(milliseconds>0 && milliseconds<=5000){double until=clockSeconds()+milliseconds/1000.0;do {CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.005,false);}while(clockSeconds()<until);}
}
