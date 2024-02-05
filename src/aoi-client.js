function zeroPad(num, size) {
	num = num.toString();
	while (num.length < size) num = "0" + num;
	return num;
}

function createMarkerElement(id, size, brightness){
	let baseUrl = "https://raw.githubusercontent.com/AprilRobotics/apriltag-imgs/master/tag36h11/";

	let image = document.createElement("img");
	image.id = "pupil-apriltag-marker-" + id;
	image.src = baseUrl + "tag36_11_" + zeroPad(id, 5) + ".png"
	image.style.width = size + "px";
	image.style.height = size + "px";
	image.style.zIndex = 999;
	image.style.imageRendering = "pixelated";
	image.style.filter = "brightness(" + (brightness*100) + "%)";
	image.style.position = "fixed";

	return image;
}

function embedTags(markerSize, brightness, target=null){
	if(!target){
		target = document.body;
	}

	let markers = [
		createMarkerElement(0, markerSize, brightness),
		createMarkerElement(1, markerSize, brightness),
		createMarkerElement(2, markerSize, brightness),
		createMarkerElement(3, markerSize, brightness),
	];

	markers[0].style.top = "0px";
	markers[0].style.left = "0px";

	markers[1].style.top = "0px";
	markers[1].style.right = "0px";

	markers[2].style.bottom = "0px";
	markers[2].style.left = "0px";

	markers[3].style.bottom = "0px";
	markers[3].style.right = "0px";

	let tagContainer = document.createElement("div");
	tagContainer.id = "apriltag-marker-root-container";
	for(let marker of markers){
		marker.className = "apriltag-marker"
		tagContainer.appendChild(marker);
	}

	target.appendChild(tagContainer);
}

function hideTags(){
	document.getElementById("apriltag-marker-root-container").style.visibility = "hidden";
}

function showTags(){
	document.getElementById("apriltag-marker-root-container").style.visibility = "visible";
}

function shimLocationEventsForSPAs(){
	// https://stackoverflow.com/a/52809105
	let oldPushState = history.pushState;
	history.pushState = function pushState() {
		let ret = oldPushState.apply(this, arguments);
		window.dispatchEvent(new Event("pushstate"));
		window.dispatchEvent(new Event("locationchange"));
		return ret;
	};

	let oldReplaceState = history.replaceState;
	history.replaceState = function replaceState() {
		let ret = oldReplaceState.apply(this, arguments);
		window.dispatchEvent(new Event("replacestate"));
		window.dispatchEvent(new Event("locationchange"));
		return ret;
	};

	window.addEventListener("popstate", () => {
		window.dispatchEvent(new Event("locationchange"));
	});
}

function installEventListeners(){
	shimLocationEventsForSPAs();

	window.addEventListener("scroll", (e) => {
		propagateScrollEvent(window.scrollX, window.scrollY);
	});

	window.addEventListener("resize", (e) => {
		propagateResizeEvent(window.innerWidth, window.innerHeight);
		propagatePageElements();
	});

	window.addEventListener("focus", (e) => {
		console.log("Focus event")
		propagateFocusEvent(window.scrollX, window.scrollY);
	});

	document.addEventListener("visibilitychange", (event) => {
		console.log("visibility event")
		if (document.visibilityState == "visible") {
			console.log("tab is active")
			propagateFocusEvent(window.scrollX, window.scrollY);
		} else {
			console.log("tab is inactive")
		}

	}, false);
	console.log("attached visibility change event handler");

	let last_sent_url = window.location.toString();
	window.addEventListener("locationchange", (e) => {
		let url = window.location.toString();
		if(url != last_sent_url){
			propagateLocationChangeEvent();
			propagatePageElements();
			last_sent_url = url;
		}
	});

	propagateLocationChangeEvent();
	propagateResizeEvent(window.innerWidth, window.innerHeight);
	propagatePageElements();

	let focusedElement = document.activeElement || document.firstElementChild();
	focusedElement.focus();
}
