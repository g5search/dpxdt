/*
 * Copyright 2013 Brett Slatkin
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// TODO: User agent spoofing shortcut

var fs = require('fs');
var system = require('system');

console.log = function(msg){
  fs.write("/tmp/phantom.log", msg + "\n", 'w');
};

var resourceWait  = 300;
var maxRenderWait = 30000;
var url           = 'https://twitter.com/#!/nodejs';
var count         = 0;
var forcedRenderTimeout;
var renderTimeout;

// Read and validate config.
var configPath = null;
var outputPath = null;
if (system.args.length == 3) {
    configPath = system.args[1];
    outputPath = system.args[2];
} else {
    console.log('Usage: phantomjs capture.js <config.js> <outputPath>');
    phantom.exit(1);
}

try {
    var config = JSON.parse(fs.read(configPath));
} catch (e) {
    console.log('Could not read config at "' + configPath + '":\n' + e);
    phantom.exit(1);
}

['targetUrl'].forEach(function(field) {
    if (!config[field]) {
        console.log('Missing required field: ' + field);
        phantom.exit(1);
    }
});


// Configure the page.
var page = require('webpage').create();

if (config.viewportSize) {
    page.viewportSize = {
        width: config.viewportSize.width,
        height: config.viewportSize.height
    };
}

if (config.userAgent) {
    page.settings.userAgent = config.userAgent;
}

if (config.clipRect) {
    page.clipRect = {
        left: 0,
        top: 0,
        width: config.clipRect.width,
        height: config.clipRect.height
    };
}

if (config.cookies) {
    config.cookies.forEach(function(cookie) {
        phantom.addCookie(cookie);
    });
}

// Add username and password as a parameter for HTTP basic auth
if (config.httpUserName && config.httpPassword) {
    page.settings.userName = config.httpUserName;
    page.settings.password = config.httpPassword;
}

page.settings.resourceTimeout = config.resourceTimeoutMs || 10000;


// Do not load Google Analytics URLs. We don't want to pollute stats.
var badResources = [
    'www.google-analytics.com'
];

if (config.resourcesToIgnore) {
    badResources.forEach(function(bad) {
        config.resourcesToIgnore.push(bad);
    });
} else {
    config.resourcesToIgnore = badResources;
}


// Echo all console messages from the page to our log.
page.onConsoleMessage = function(message, line, source) {
    console.log('>> CONSOLE: ' + message);
};


var ResourceStatus = {
    DONE: 'done',
    ERROR: 'error',
    TIMEOUT: 'timeout',
    PENDING: 'pending'
};

// Maps a URL to a ResultStatus value.
var resourceStatusMap = {};


// We don't necessarily want to load every resource a page asks for.
page.onResourceRequested = function(requestData, networkRequest) {
    var url = requestData.url;

    if (url.indexOf('data:') == 0) {
        console.log('Requested data URI');
    } else {
        for (var i = 0; i < config.resourcesToIgnore.length; i++) {
            var bad = config.resourcesToIgnore[i];
            if (bad == url || url.match(new RegExp(bad))) {
                console.log('Blocking resource: ' + url);
                networkRequest.abort();
                return;
            }
        }

        if (config.injectHeaders) {
            for (var host in config.injectHeaders) {
                if (host == url || url.match(new RegExp(host))) {
                    var headers = config.injectHeaders[host];
                    for (var header in headers) {
                        networkRequest.setHeader(header, headers[header]);
                        console.log('Setting header ' + header + ' to ' + headers[header]);
                    }
                }
            }
        }
        console.log('Requested: ' + url);
    }

    // Always reset the status to pending each time a new request happens.
    // This handles the case where the page or JS causes a resource to reload
    // for some reason, expecting a different result.
    resourceStatusMap[url] = ResourceStatus.PENDING;
};


// Log all resources loaded as part of this request, for debugging.
page.onResourceReceived = function(response) {
    if (response.stage != 'end') {
        return;
    }
    var url = response.url;
    if (url.indexOf('data:') == 0) {
        console.log('Loaded data URI');
    } else if (response.redirectURL) {
        console.log('Loaded redirect: ' + url + ' -> ' + response.redirectURL);
    } else {
        console.log('Loaded: ' + url);
    }
    if (resourceStatusMap[url] == ResourceStatus.PENDING) {
        resourceStatusMap[url] = ResourceStatus.DONE;
    }
};


// Detect if any resources timeout.
page.onResourceTimeout = function(request) {
    var url = request.url;
    console.log('Loading resource timed out: ' + url);
    if (resourceStatusMap[url] == ResourceStatus.PENDING) {
        resourceStatusMap[url] = ResourceStatus.TIMEOUT;
    }
};


// Detect if any resources fail to load.
page.onResourceError = function(error) {
    var url = error.url;
    console.log('Loading resource errored: ' + url +
                ', errorCode=' + error.errorCode +
                ', errorString=' + error.errorString);
    if (resourceStatusMap[url] == ResourceStatus.PENDING) {
        resourceStatusMap[url] = ResourceStatus.ERROR;
    }
};


// Just for debug logging.
page.onInitialized = function() {
    console.log('page.onInitialized');
};


// Dumps out any error logs.
page.onError = function(msg, trace) {
    var msgStack = [msg];
    if (trace && trace.length) {
        trace.forEach(function(t) {
            msgStack.push(
                ' -> ' + (t.file || t.sourceURL) + ': ' + t.line +
                (t.function ? ' (in function ' + t.function + ')' : ''));
        });
    }

    console.log('page.onError', msgStack.join('\n'));
};


// Just for debug logging.
page.onNavigationRequested = function(url, type, willNavigate, main) {
    if (!main) {
        return;
    }
    console.log('page.onNavigationRequested: ' + url);
};


// Just for debug logging.
page.onLoadStarted = function() {
    console.log('page.onLoadStarted');
};


// Just for debug logging.
page.onLoadFinished = function(status) {
    console.log('page.onLoadFinished');
    if (status == 'success') {
        console.log('Loaded the page successfully');
    } else {
        console.log('Loading the page failed', status);
        phantom.exit(1);
    }
};


// Takes the screenshot and exits successfully.
page.doScreenshot = function() {
    console.log('Taking the screenshot and saving to:', outputPath);
    phantom.injectJs(system.env['INJECT_DIR'] + '/inject.js');
    console.log('starting timeout before render');

    // window.setTimeout(function() {
      console.log('timeout finsihed rendering');
      page.render(outputPath);
    // }, 5000);

    phantom.exit(0);
};


// Injects CSS and JS into the page.
page.doInject = function() {
    var didInject = false;

    if (config.injectCss) {
        didInject = true;
        console.log('Injecting CSS: ' + config.injectCss);
        page.evaluate(function(config) {
            var styleEl = document.createElement('style');
            styleEl.type = 'text/css';
            styleEl.innerHTML = config.injectCss;
            document.getElementsByTagName('head')[0].appendChild(styleEl);
        }, config);
    }

    if (config.injectJs) {
        didInject = true;
        console.log('Injecting JS: ' + config.injectJs);
        var success = page.evaluate(function(config) {
            try {
                window.eval(config.injectJs);
            } catch (e) {
                console.log('Exception running injectJs');
                console.log(e.stack);
                return false;
            }
            return true;
        }, config);
        if (!success) {
            phantom.exit(1);
        }
    }
    console.log('doinject done, waiting');

    // setTimeout(function() {
    page.waitForReady(page.doScreenshot);
    // }, 2000);
};


// Wait for all resources on the page to load, then call the given function.
page.waitForReady = function(func) {
    var totals = {};
    for (var url in resourceStatusMap) {
        var status = resourceStatusMap[url];
        var value = totals[status] || 0;
        totals[status] = value + 1;
    }

    console.log('Status of all resources:', JSON.stringify(totals));

    var pending = totals[ResourceStatus.PENDING] || 0;
    if (!pending) {
        console.log('No more resources are pending. get funcy!');
        func();
        return;
    } else {
        for (var url in resourceStatusMap) {
            if (resourceStatusMap[url] == ResourceStatus.PENDING) {
                console.log('Still waiting for: ' + url);
            }
        }
    }

    setTimeout(function() {
        page.waitForReady(func);
    }, 500);
};

page.isDone = function(){
  return page.evaluate(function() {

    if(document.readyState != "complete"){
      return false;
    }

    var llis = document.getElementsByTagName('img');
    var t_flag = true;

    for(var lcv = 0; lcv < llis.length; lcv++){

      var imgElement = llis[lcv];

      if(
        typeof imgElement == 'undefined' ||
        typeof imgElement.complete == 'undefined' ||
        typeof imgElement.naturalHeight == 'undefined' ||
        !(imgElement.complete && imgElement.naturalHeight != 0)
      ){
        return false;
      }
    }

    if( document.getElementsByClassName('lazy-load').length !=
        document.getElementsByClassName('lazy-load is-unveiled').length
      ){
      return false;
    }

    return true;
  });
};

page.onLoadFinished = function(status){
  //Check for completion every second
  var iid = setInterval(function(){
    console.log('interval run');
    if(page.isDone()){
      console.log('page done');
      clearInterval(iid);
      page.waitForReady(page.doInject);
    }
  }, 1000);
}

// Kickoff the load!
console.log('Opening page', config.targetUrl);

page.open(config.targetUrl, function(status) {
  console.log('page opened');
});
