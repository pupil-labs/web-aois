window.addEventListener('DOMContentLoaded', () => {
  let currentTarget = null;
  let addAOIButton = null;
  let aoiListContainer = null;

  function getXPath(element) {
    if (element.id !== '') {
      return 'id("' + element.id + '")';
    }
    if (element === document.body) {
      return "//body";
    }
    let ix = 0;
    const siblings = element.parentNode.childNodes;
    for (let i = 0; i < siblings.length; i++) {
      const sibling = siblings[i];
      if (sibling === element) {
        return getXPath(element.parentNode) + '/' + element.tagName.toLowerCase() + '[' + (ix + 1) + ']';
      }
      if (sibling.nodeType === 1 && sibling.tagName === element.tagName) {
        ix++;
      }
    }
  }

  function getElementByXpath(path) {
    return document.evaluate(path, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
  }

  function findPageList(){
    for(let bullet of aoiListContainer.childNodes){
      if(bullet.childNodes[0].nodeValue == window.location.href){
        return bullet.subList;
      }
    }

    let bullet = document.createElement('li');
    bullet.classList.add("web_aoi_ignore_hovers");
    bullet.title = window.location.href;
    bullet.innerText = window.location.href;
    bullet.subList = document.createElement('ul');
    bullet.subList.title = '';
    bullet.subList.classList.add("web_aoi_ignore_hovers");
    bullet.appendChild(bullet.subList);

    aoiListContainer.appendChild(bullet);
    return bullet.subList;
  }

  function createAOI(){
    let aoi_id = prompt("Please provide a unique identifier for this AOI. Avoid special characters.", currentTarget.id || currentTarget.alt);
    if (aoi_id) {
      let item = document.createElement('li');
      item.classList.add("web_aoi_ignore_hovers");
      item.dataset.target = getXPath(currentTarget);
      item.innerText = aoi_id;
      item.title = aoi_id;
      item.addEventListener('click', (e) => {
        let element = getElementByXpath(item.dataset.target);
        element.scrollIntoView({ behavior: 'smooth' });
      });

      findPageList().appendChild(item);
    }
  }

  function download(){
    let data = {};
    for(let urlBullet of aoiListContainer.childNodes){
        let aois = {};
        for(let aoiBullet of urlBullet.subList.childNodes){
          aois[aoiBullet.innerText] = [{
            "type": "locator",
            "args": {
              "selector": "xpath=" + aoiBullet.dataset.target
            }
          }];
        }
        data[urlBullet.childNodes[0].nodeValue] = aois;
    };

    web_aois_save_definitions(data);
  }

  function init(){
    let stylesheet = document.createElement('style');
    stylesheet.innerHTML = `
      @keyframes glow {
        0% {
          filter: opacity(100%);
        }
        75% {
          filter: opacity(100%);
        }
        100% {
          filter: opacity(85%);
        }
      }

      #web_aoi_add_button {
        position: absolute;
        top: 0;
        left: 0;
        color: #000;
        z-index: 9999;
        border-radius: 5px;
        pointer-events: none;
        animation: glow 0.75s infinite alternate;
        background-color: rgba(128, 24, 24, 0.75);
        box-shadow: 0 0 10px 10px rgba(128, 24, 24, 0.75);
        border: 0;
      }

      #web_aoi_add_button > div {
        display: inline-block;
        padding: 0.25em 0.5em;
        background: rgba(192, 192, 192, 1.0);
        border-radius: 5px;
      }

      #web_aoi_list_box {
        position: fixed;
        top: 2em;
        right: 2em;
        background: #7ac;
        color: #222;
        box-shadow: 0 0 5px 5px rgba(0, 0, 0, 0.75);
        z-index: 9999;
        padding: 0.5em;
        border-radius: 0.5em;
        max-width: 300px;
      }

      #web_aoi_list_box > h3 {
        padding: 0.25em 0.5em;
        font-size: 14pt;
        margin: 0;
        font-weight: bold;
        border-bottom: 3px double #000;
      }

      #web_aoi_list_box ul {
        margin: 0;
        padding: 0 1em;
        list-style: disc;
        list-style-position: inside;
      }

      #web_aoi_list_box li {
        text-overflow: ellipsis;
        white-space: nowrap;
        overflow: hidden;
      }

      #web_aoi_list_box > button {
        padding: 0.25em 0.5em;
        margin-top: 0.5em;
        background: #9ce;
        width: 100%;
        border: 1px solid #38a;
        border-radius: 4px;
      }
    `;
    document.head.appendChild(stylesheet);

    let aoiListBox = document.createElement('div');
    aoiListBox.id = "web_aoi_list_box";
    aoiListBox.classList.add("web_aoi_ignore_hovers");
    aoiListBox.innerHTML = '<h3 class="web_aoi_ignore_hovers">Areas of Interest</h3>';
    document.body.appendChild(aoiListBox);

    aoiListContainer = document.createElement('ul');
    aoiListContainer.classList.add("web_aoi_ignore_hovers");
    aoiListBox.appendChild(aoiListContainer);

    let downloadButton = document.createElement('button');
    downloadButton.innerText = 'Save';
    downloadButton.classList.add("web_aoi_ignore_hovers");
    downloadButton.addEventListener('click', (e) => download());
    aoiListBox.appendChild(downloadButton);

    addAOIButton = document.createElement('button');
    addAOIButton.onclick = (e) => createAOI();
    addAOIButton.id = "web_aoi_add_button";
    addAOIButton.innerHTML = "<div>Right-click to create AOI</div>";
    addAOIButton.style.display = 'none';
    addAOIButton.classList.add("web_aoi_ignore_hovers");
    document.body.appendChild(addAOIButton);

    document.addEventListener('mouseover', function (event) {
      setCurrent(event.target);
    });

    document.addEventListener('mouseout', function (event) {
      if(event.target == addAOIButton){
        return;
      }
      addAOIButton.style.display = 'none';
    });

    document.addEventListener('contextmenu', function (event) {
      createAOI();
      event.preventDefault();
      return false;
    });

    document.addEventListener('keyup', function (event) {
      if(!currentTarget){
        return;
      }
      if(event.key == "p" || event.key == "P"){
        let currentRect = currentTarget.getBoundingClientRect();
        let targetRect = currentRect;
        while(sameRect(currentRect, targetRect)){
          currentTarget = currentTarget.parentNode;
          targetRect = currentTarget.getBoundingClientRect();
        }
        setCurrent(currentTarget);
      }
    });
  }

  function setCurrent(target){
    if(target.classList.contains("web_aoi_ignore_hovers")){
      return;
    }

    currentTarget = target;

    let targetRect = target.getBoundingClientRect();

    addAOIButton.style.top = (window.scrollY + targetRect.top + 2 ) + 'px';
    addAOIButton.style.left = (window.scrollX + targetRect.left + 2) + 'px';
    addAOIButton.style.width = (targetRect.width - 4) + 'px';
    addAOIButton.style.height = (targetRect.height - 4) + 'px';
    addAOIButton.style.display = '';

    target.hrefOld = target.href;
  }

  function sameRect(a, b){
    return a.x == b.x && a.y == b.y && a.width == b.width && a.height == b.height;
  }

  init();
});
