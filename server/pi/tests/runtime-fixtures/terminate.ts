// Windows has no portable external graceful SIGTERM. Emit the registered
// signal inside the real host process so the same cleanup handler is exercised.
setInterval(()=>process.emit("SIGTERM"),1000).unref();
